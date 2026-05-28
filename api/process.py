from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
import fitz
import re
import zipfile
import io
import json

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Files"],
)


def limpar_nome(nome):
    nome = nome.replace("\n", " ").replace("\r", " ")
    nome = re.sub(r'\s+', ' ', nome)
    nome = re.sub(r'[\\/*?:"<>|]', "", nome)
    return nome.strip()


def salvar_pdf_memoria(doc, paginas):
    novo = fitz.open()
    for p in paginas:
        novo.insert_pdf(doc, from_page=p, to_page=p)
    buf = io.BytesIO()
    novo.save(buf)
    novo.close()
    buf.seek(0)
    return buf.getvalue()


def processar_ficha(doc):
    resultados = []
    grupo, nome_atual = [], None

    for i, p in enumerate(doc):
        texto = p.get_text()
        linhas = [l.strip() for l in texto.split("\n") if l.strip()]
        nome_encontrado = None

        for idx, linha in enumerate(linhas):
            if "MATRICULA" in linha.upper():
                for j in range(idx - 1, max(idx - 6, 0), -1):
                    cand = linhas[j]
                    if re.fullmatch(r"[A-ZÁ-Ú\s]+", cand):
                        if 2 <= len(cand.split()) <= 6 and "MAE" not in cand and "PAI" not in cand:
                            nome_encontrado = cand
                            break

        if nome_encontrado:
            if grupo and nome_atual:
                nome_arq = limpar_nome(nome_atual + " FRE") + ".pdf"
                resultados.append((nome_arq, salvar_pdf_memoria(doc, grupo)))
                grupo = []
            nome_atual = nome_encontrado

        grupo.append(i)

    if grupo and nome_atual:
        nome_arq = limpar_nome(nome_atual + " FRE") + ".pdf"
        resultados.append((nome_arq, salvar_pdf_memoria(doc, grupo)))

    return resultados


def processar_trct(doc):
    resultados = []
    grupo, nome = [], "Desconhecido"

    for i, p in enumerate(doc):
        texto = p.get_text()
        linhas = [l.strip() for l in texto.split("\n") if l.strip()]

        if "TERMO RESCIS" in texto.upper():
            if grupo:
                nome_arq = limpar_nome(nome + " TRCT") + ".pdf"
                resultados.append((nome_arq, salvar_pdf_memoria(doc, grupo)))
                grupo = []

            for idx, linha in enumerate(linhas):
                if "11" in linha and "NOME" in linha.upper():
                    for j in range(idx - 1, max(idx - 6, 0), -1):
                        cand = linhas[j]
                        if re.fullmatch(r"[A-ZÁ-Ú\s]+", cand):
                            if 2 <= len(cand.split()) <= 5 and "MAE" not in cand:
                                nome = cand
                                break

        grupo.append(i)

    if grupo:
        nome_arq = limpar_nome(nome + " TRCT") + ".pdf"
        resultados.append((nome_arq, salvar_pdf_memoria(doc, grupo)))

    return resultados


def processar_seguro(doc):
    from collections import defaultdict
    grupos = defaultdict(list)

    for i, p in enumerate(doc):
        texto = p.get_text().upper()
        candidatos = re.findall(r"[A-ZÁ-Ú\s]{15,}", texto[:1000])
        nome = "Desconhecido"

        for n in candidatos:
            n = n.strip()
            if any(p in n for p in ["REQUERIMENTO", "SEGURO", "BENEFICIO", "DECLARO", "DIREITO"]):
                continue
            if 3 <= len(n.split()) <= 6:
                nome = n
                break

        grupos[limpar_nome(nome)].append(i)

    resultados = []
    for nome, paginas in grupos.items():
        nome_arq = limpar_nome(nome + " SD") + ".pdf"
        resultados.append((nome_arq, salvar_pdf_memoria(doc, paginas)))

    return resultados


def processar_ponto(doc):
    resultados = []

    for i, p in enumerate(doc):
        texto = p.get_text()
        nome = "Desconhecido"

        for l in texto.split("\n"):
            l = l.strip()
            if re.fullmatch(r"[A-ZÁ-Ú\s]+", l) and 3 <= len(l.split()) <= 6:
                nome = l
                break

        nome_arq = limpar_nome(nome + " PONTO RM") + ".pdf"
        resultados.append((nome_arq, salvar_pdf_memoria(doc, [i])))

    return resultados


def processar_informe(doc):
    resultados = []
    grupo, cpf = [], "SEM_CPF"

    for i, p in enumerate(doc):
        texto = p.get_text()

        if "INFORME DE RENDIMENTOS" in texto.upper():
            if grupo:
                nome_arq = limpar_nome(cpf) + ".pdf"
                resultados.append((nome_arq, salvar_pdf_memoria(doc, grupo)))
                grupo = []

            m = re.search(r'\d{3}\.\d{3}\.\d{3}-\d{2}', texto)
            if m:
                cpf = re.sub(r'\D', '', m.group())

        grupo.append(i)

    if grupo:
        nome_arq = limpar_nome(cpf) + ".pdf"
        resultados.append((nome_arq, salvar_pdf_memoria(doc, grupo)))

    return resultados


HANDLERS = {
    "ficha": processar_ficha,
    "trct": processar_trct,
    "seguro": processar_seguro,
    "ponto": processar_ponto,
    "informe": processar_informe,
}


@app.post("/api/process")
async def process(file: UploadFile = File(...), tipo: str = Form(...)):
    if tipo not in HANDLERS:
        return Response(status_code=400, content=b"Tipo invalido")

    contents = await file.read()
    doc = fitz.open(stream=contents, filetype="pdf")

    resultados = HANDLERS[tipo](doc)
    doc.close()

    if not resultados:
        return Response(status_code=422, content=b"Nenhum documento identificado no PDF")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zipf:
        for nome, pdf_bytes in resultados:
            zipf.writestr(nome, pdf_bytes)

    zip_buf.seek(0)
    nomes = [nome for nome, _ in resultados]

    return Response(
        content=zip_buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=documentos.zip",
            "X-Files": json.dumps(nomes, ensure_ascii=False),
        },
    )
