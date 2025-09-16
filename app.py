# app.py
import os
import requests
import unicodedata
import time # Importado para controlar o cache
from flask import Flask, render_template, request, flash, redirect, url_for
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# Cria a instância da aplicação Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'uma-chave-secreta-muito-segura')

# --- Configurações da API Sophia ---
SOPHIA_TENANT = os.getenv('SOPHIA_TENANT')
SOPHIA_USER = os.getenv('SOPHIA_USER')
SOPHIA_PASSWORD = os.getenv('SOPHIA_PASSWORD')
SOPHIA_API_HOSTNAME = os.getenv('SOPHIA_API_HOSTNAME', 'portal.sophia.com.br')
SOPHIA_API_BASE_URL = f"https://{SOPHIA_API_HOSTNAME}/SophiAWebApi/{SOPHIA_TENANT}"


# --- Sistema de Cache ---
# Cache para o Token da API
api_token_cache = {
    "token": None,
    "expires_at": 0
}
# Cache para os Resultados da Busca
search_cache = {}
CACHE_DURATION_SECONDS = 60 # Guardar resultados por 60 segundos


# --- Funções Auxiliares ---

def get_sophia_token():
    """Obtém um token da API, usando o cache para evitar chamadas repetidas."""
    if api_token_cache["token"] and time.time() < api_token_cache["expires_at"]:
        return api_token_cache["token"]

    auth_url = SOPHIA_API_BASE_URL + "/api/v1/Autenticacao"
    auth_data = {"usuario": SOPHIA_USER, "senha": SOPHIA_PASSWORD}
    try:
        response = requests.post(auth_url, json=auth_data, timeout=10)
        response.raise_for_status()
        
        new_token = response.text
        api_token_cache["token"] = new_token
        api_token_cache["expires_at"] = time.time() + (29 * 60) # Validade de 29 minutos
        
        return new_token
    except requests.exceptions.RequestException as e:
        print(f"Erro ao obter token da API: {e}")
        return None

def buscar_foto(session, base_url, headers):
    """Função genérica para buscar uma foto de forma segura."""
    try:
        response = session.get(base_url, headers=headers, timeout=5)
        if response.status_code == 200 and response.text:
            return response.json().get('foto')
    except requests.exceptions.RequestException:
        pass
    return None

def buscar_foto_aluno(codigo_aluno, headers):
    """Busca a foto de um aluno específico."""
    url = SOPHIA_API_BASE_URL + f"/api/v1/alunos/{codigo_aluno}/Fotos/FotosReduzida"
    with requests.Session() as session:
        foto = buscar_foto(session, url, headers)
    return codigo_aluno, foto

def buscar_foto_responsavel(codigo_resp, headers):
    """Busca a foto de um responsável específico."""
    url = SOPHIA_API_BASE_URL + f"/api/v1/responsaveis/{codigo_resp}/fotos/FotoReduzida"
    with requests.Session() as session:
        foto = buscar_foto(session, url, headers)
    return codigo_resp, foto

def normalizar_texto(texto):
    """Remove acentos e converte para minúsculas para facilitar comparações."""
    if not texto: return ""
    nfkd_form = unicodedata.normalize('NFKD', texto.lower())
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)])


# --- Rotas da Aplicação ---

@app.route('/')
def index():
    """Renderiza a página inicial de busca."""
    return render_template('index.html')

@app.route('/buscar', methods=['POST'])
def buscar_aluno():
    """Processa a busca de alunos, usa cache e filtra por nome/sobrenome."""
    termo_busca = request.form.get('nome_aluno', '').strip()

    cache_key = termo_busca.lower()
    if cache_key in search_cache and time.time() < search_cache[cache_key]['expires_at']:
        print("Resultado encontrado no cache!")
        return render_template('index.html', **search_cache[cache_key]['data'])
    
    token = get_sophia_token()
    if not token:
        flash('Erro de autenticação com o sistema Sophia.', 'error')
        return render_template('index.html', alunos=[])

    headers = {'token': token}
    search_url = SOPHIA_API_BASE_URL + "/api/v1/Alunos"
    primeiro_nome = termo_busca.split(' ')[0]
    params = {'Nome': primeiro_nome}
    
    try:
        response = requests.get(search_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        alunos_da_api = response.json()

        termos_normalizados = [normalizar_texto(term) for term in termo_busca.split()]
        alunos_filtrados = []
        for aluno in alunos_da_api:
            nome_aluno_normalizado = normalizar_texto(aluno.get('nome', ''))
            if all(term in nome_aluno_normalizado for term in termos_normalizados):
                alunos_filtrados.append(aluno)

        fotos_mapeadas = {}
        codigos_alunos = [aluno['codigo'] for aluno in alunos_filtrados]
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(buscar_foto_aluno, codigo, headers) for codigo in codigos_alunos]
            for future in futures:
                codigo, foto_uri = future.result()
                if foto_uri:
                    fotos_mapeadas[codigo] = foto_uri
        
        for aluno in alunos_filtrados:
            aluno['foto_uri'] = fotos_mapeadas.get(aluno.get('codigo'))

        if not alunos_filtrados:
            flash(f'Nenhum aluno encontrado para "{termo_busca}".', 'info')
        
        template_data = {'alunos': alunos_filtrados, 'busca_anterior': termo_busca}
        search_cache[cache_key] = {
            'data': template_data,
            'expires_at': time.time() + CACHE_DURATION_SECONDS
        }
        
        return render_template('index.html', **template_data)

    except requests.exceptions.RequestException as e:
        flash(f'Erro ao se comunicar com a API: {e}', 'error')
        return render_template('index.html', alunos=[])

@app.route('/aluno/<int:aluno_id>')
def detalhes_aluno(aluno_id):
    """Exibe a página de detalhes com TODAS as pessoas autorizadas e regras de saída."""
    token = get_sophia_token()
    if not token:
        flash('Erro de autenticação ao buscar detalhes.', 'error')
        return redirect(url_for('index'))
    
    headers = {'token': token}
    try:
        url_responsaveis = SOPHIA_API_BASE_URL + f"/api/v1/alunos/{aluno_id}/responsaveis"
        resp_responsaveis = requests.get(url_responsaveis, headers=headers, timeout=10).json()

        url_autorizacao = SOPHIA_API_BASE_URL + f"/api/v1/alunos/{aluno_id}/AutorizacaoRetirada"
        dados_autorizacao = requests.get(url_autorizacao, headers=headers, timeout=10).json()

        aluno_url = SOPHIA_API_BASE_URL + f"/api/v1/Alunos/{aluno_id}"
        dados_aluno = requests.get(aluno_url, headers=headers).json()
        aluno_nome = dados_aluno.get('nome', 'Aluno não encontrado')

        _, aluno_foto_uri = buscar_foto_aluno(aluno_id, headers)

        regras_saida = {
            'acompanhado': dados_autorizacao.get('deixarEscolaAcompanhado', False),
            'sozinho': dados_autorizacao.get('deixarEscolaSozinho', False),
            'conducao': dados_autorizacao.get('deixarEscolaConducaoEscolar', False),
            'fora_escola': dados_autorizacao.get('aguardarForaEscola', False),
            'horario_regular': dados_autorizacao.get('autorizarSaidaTerminoHorarioRegular', False),
            'atividade_extra': dados_autorizacao.get('autorizarSaidaTerminoAtividadeExtra', False)
        }
        
        pais_e_maes_bruto, outras_pessoas_bruto = [], []
        
        # Processa a lista de responsáveis diretos
        for resp in resp_responsaveis:
            if resp and resp.get('retiradaAutorizada'):
                tipo_vinculo = resp.get('tipoVinculo')
                if tipo_vinculo and tipo_vinculo.get('descricao') in ['Pai', 'Mãe']:
                    pais_e_maes_bruto.append(resp)
                else:
                    outras_pessoas_bruto.append(resp)
        
        # Adiciona a lista de "outras pessoas autorizadas"
        for pessoa in dados_autorizacao.get('outrasPessoas', []):
            outras_pessoas_bruto.append(pessoa)

        # Coleta todos os códigos para buscar as fotos
        codigos_para_buscar_foto = {p.get('codigo') for p in pais_e_maes_bruto + outras_pessoas_bruto if p.get('codigo')}

        # Busca todas as fotos em paralelo
        fotos_mapeadas = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(buscar_foto_responsavel, codigo, headers) for codigo in codigos_para_buscar_foto]
            for future in futures:
                codigo, foto_uri = future.result()
                if foto_uri:
                    fotos_mapeadas[codigo] = foto_uri
        
        # Atribui as fotos e filtra o nome do próprio aluno
        for pessoa_lista in [pais_e_maes_bruto, outras_pessoas_bruto]:
            for pessoa in pessoa_lista:
                pessoa['foto_uri'] = fotos_mapeadas.get(pessoa.get('codigo'))

        pais_e_maes = [p for p in pais_e_maes_bruto if p.get('nome', '').lower() != aluno_nome.lower()]
        outras_pessoas = [p for p in outras_pessoas_bruto if p.get('nome', '').lower() != aluno_nome.lower()]

        return render_template('detalhes_aluno.html',
            aluno_nome=aluno_nome,
            aluno_foto_uri=aluno_foto_uri,
            pais_e_maes=pais_e_maes,
            outras_pessoas=outras_pessoas,
            regras_saida=regras_saida
        )
    except requests.exceptions.RequestException as e:
        flash(f"Erro ao buscar detalhes do aluno: {e}", "error")
        return redirect(url_for('index'))

# --- Inicialização do Servidor ---
if __name__ == '__main__':
    app.run(debug=True)
