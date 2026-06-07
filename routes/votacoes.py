"""
routes/votacoes.py — CRUD de votações + fluxo de votação
"""
import subprocess
import sys
import os
from datetime import datetime, timezone

from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, jsonify, session)
from flask_login import login_required, current_user
from bson import ObjectId

from models.votacao import VotacaoModel, CandidatoModel, VotoModel, LogModel, ServidorModel
from models.user import UserModel
from core.port_manager import alocar_portas
from core import obter_urna, enviar_voto, status_coordenador, encerrar_votacao, status_contador

votacoes_bp = Blueprint("votacoes", __name__)


# ---------------------------------------------------------------------------
# Helper — parser de data/hora do formulário
# ---------------------------------------------------------------------------
def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lista de votações
# ---------------------------------------------------------------------------
@votacoes_bp.route("/")
@votacoes_bp.route("/votacoes")
def lista():
    publicas    = VotacaoModel.listar_publicas()
    minhas      = VotacaoModel.listar_por_criador(current_user.id) if current_user.is_authenticated else []
    return render_template("votacoes/lista.html", publicas=publicas, minhas=minhas)


# ---------------------------------------------------------------------------
# Criar votação
# ---------------------------------------------------------------------------
@votacoes_bp.route("/votacoes/criar", methods=["GET", "POST"])
@login_required
def criar():
    if request.method == "POST":
        titulo     = request.form.get("titulo", "").strip()
        descricao  = request.form.get("descricao", "").strip()
        inicio_str = request.form.get("inicio")
        fim_str    = request.form.get("fim")
        publica    = request.form.get("publica") == "on"
        senha      = request.form.get("senha_acesso", "").strip() or None
        qtd_urnas  = int(request.form.get("qtd_urnas", 2))
        qtd_cont   = int(request.form.get("qtd_contadores", 2))

        # Candidatos
        nomes_cand = request.form.getlist("candidato_nome[]")
        nums_cand  = request.form.getlist("candidato_numero[]")
        desc_cand  = request.form.getlist("candidato_desc[]")

        candidatos = [
            {"nome": n.strip(), "numero": int(nums_cand[i]) if nums_cand[i].isdigit() else i+1,
             "descricao": desc_cand[i].strip() if i < len(desc_cand) else ""}
            for i, n in enumerate(nomes_cand) if n.strip()
        ]

        if not titulo or not candidatos or len(candidatos) < 2:
            flash("Preencha título e pelo menos 2 candidatos/opções.", "danger")
            return render_template("votacoes/criar.html")

        inicio = _parse_dt(inicio_str)
        fim    = _parse_dt(fim_str)

        # Criar votação no MongoDB
        vid = VotacaoModel.criar({
            "titulo":         titulo,
            "descricao":      descricao,
            "criador_id":     current_user.id,
            "inicio":         inicio,
            "fim":            fim,
            "publica":        publica,
            "senha":          senha,
            "candidatos":     candidatos,
            "qtd_urnas":      qtd_urnas,
            "qtd_contadores": qtd_cont,
        })
        votacao_id_str = str(vid)

        # Criar candidatos
        CandidatoModel.criar_multiplos(vid, candidatos)

        # Alocar portas
        portas = alocar_portas(votacao_id_str, qtd_urnas, qtd_cont)

        if not portas["coordenador"]:
            flash("Não foi possível alocar portas para esta votação.", "danger")
            VotacaoModel.atualizar(vid, {"status": "erro"})
            return redirect(url_for("votacoes.lista"))

        # Spawnar coordenador
        coord_script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "core", "coordenador.py"
        )
        args = (
            [sys.executable, coord_script, votacao_id_str, str(portas["coordenador"])]
            + [str(p) for p in portas["urnas"]]
            + ["--"]
            + [str(p) for p in portas["contadores"]]
        )
        proc = subprocess.Popen(args)

        VotacaoModel.atualizar(vid, {
            "porta_coordenador": portas["coordenador"],
            "portas_urnas":      portas["urnas"],
            "portas_contadores": portas["contadores"],
            "pid_coordenador":   proc.pid,
        })

        LogModel.registrar(vid, "sistema", "info",
                           f"Votação criada. Coordenador pid={proc.pid}, "
                           f"urnas={portas['urnas']}, contadores={portas['contadores']}")

        flash(f"Votação criada! Infraestrutura distribuída sendo inicializada...", "success")
        return redirect(url_for("votacoes.detalhe", vid=votacao_id_str))

    return render_template("votacoes/criar.html")


# ---------------------------------------------------------------------------
# Detalhe de votação
# ---------------------------------------------------------------------------
@votacoes_bp.route("/votacoes/<vid>")
def detalhe(vid: str):
    votacao = VotacaoModel.por_id(vid)
    if not votacao:
        flash("Votação não encontrada.", "danger")
        return redirect(url_for("votacoes.lista"))

    # Senha? Verificar na sessão
    if votacao.get("senha") and not current_user.is_authenticated:
        if session.get(f"acesso_votacao_{vid}") != "ok":
            return redirect(url_for("votacoes.entrar", vid=vid))

    candidatos    = CandidatoModel.por_votacao(vid)
    urnas         = ServidorModel.urnas_por_votacao(vid)
    contadores    = ServidorModel.contadores_por_votacao(vid)
    logs          = LogModel.recentes(vid, 20)
    ja_votou      = False
    contagem      = {}

    if current_user.is_authenticated:
        ja_votou = VotoModel.eleitor_ja_votou(vid, current_user.id)
        contagem = VotoModel.contagem_por_candidato(vid)

    # Montar contagem com nomes
    contagem_display = []
    total = sum(contagem.values()) or 1
    for c in candidatos:
        cid = str(c["_id"])
        votos = contagem.get(cid, 0)
        contagem_display.append({
            "candidato": c,
            "votos":     votos,
            "pct":       round(votos / total * 100, 1),
        })

    is_criador = (current_user.is_authenticated and
                  str(votacao.get("criador_id")) == current_user.id)

    return render_template(
        "votacoes/detalhe.html",
        votacao=votacao,
        candidatos=candidatos,
        urnas=urnas,
        contadores=contadores,
        logs=logs,
        ja_votou=ja_votou,
        contagem_display=contagem_display,
        total_votos=votacao.get("total_votos", 0),
        is_criador=is_criador,
    )


# ---------------------------------------------------------------------------
# Acesso com senha
# ---------------------------------------------------------------------------
@votacoes_bp.route("/votacoes/<vid>/entrar", methods=["GET", "POST"])
def entrar(vid: str):
    votacao = VotacaoModel.por_id(vid)
    if not votacao:
        return redirect(url_for("votacoes.lista"))

    if request.method == "POST":
        senha_inf = request.form.get("senha", "")
        if senha_inf == votacao.get("senha"):
            session[f"acesso_votacao_{vid}"] = "ok"
            return redirect(url_for("votacoes.detalhe", vid=vid))
        flash("Senha incorreta.", "danger")

    return render_template("votacoes/entrar.html", votacao=votacao)


# ---------------------------------------------------------------------------
# Votar
# ---------------------------------------------------------------------------
@votacoes_bp.route("/votacoes/<vid>/votar", methods=["GET", "POST"])
@login_required
def votar(vid: str):
    votacao = VotacaoModel.por_id(vid)
    if not votacao:
        flash("Votação não encontrada.", "danger")
        return redirect(url_for("votacoes.lista"))

    if votacao.get("status") != "ativa":
        flash("Esta votação não está ativa.", "warning")
        return redirect(url_for("votacoes.detalhe", vid=vid))

    if VotoModel.eleitor_ja_votou(vid, current_user.id):
        flash("Você já votou nesta votação.", "info")
        return redirect(url_for("votacoes.detalhe", vid=vid))

    candidatos = CandidatoModel.por_votacao(vid)

    if request.method == "POST":
        candidato_id = request.form.get("candidato_id")
        if not candidato_id:
            flash("Selecione um candidato.", "danger")
            return render_template("votacoes/votar.html", votacao=votacao, candidatos=candidatos)

        porta_coord = votacao.get("porta_coordenador")
        if not porta_coord:
            flash("Infraestrutura da votação não disponível.", "danger")
            return redirect(url_for("votacoes.detalhe", vid=vid))

        # Obter urna balanceada
        porta_urna = obter_urna(porta_coord)
        if not porta_urna:
            flash("Nenhuma urna disponível no momento. Tente novamente.", "danger")
            return render_template("votacoes/votar.html", votacao=votacao, candidatos=candidatos)

        # Enviar voto
        resp = enviar_voto(porta_urna, vid, current_user.id, candidato_id)

        if resp.get("status") == "ok":
            flash(" Voto registrado com sucesso!", "success")
            return redirect(url_for("votacoes.confirmacao", vid=vid, voto_id=resp.get("voto_id", "")))
        else:
            flash(f"Erro ao votar: {resp.get('motivo', 'Falha desconhecida')}", "danger")

    return render_template("votacoes/votar.html", votacao=votacao, candidatos=candidatos)


# ---------------------------------------------------------------------------
# Confirmação de voto
# ---------------------------------------------------------------------------
@votacoes_bp.route("/votacoes/<vid>/confirmacao")
@login_required
def confirmacao(vid: str):
    votacao  = VotacaoModel.por_id(vid)
    voto_id  = request.args.get("voto_id", "")
    return render_template("votacoes/confirmacao.html", votacao=votacao, voto_id=voto_id)


# ---------------------------------------------------------------------------
# Encerrar votação (criador)
# ---------------------------------------------------------------------------
@votacoes_bp.route("/votacoes/<vid>/encerrar", methods=["POST"])
@login_required
def encerrar(vid: str):
    votacao = VotacaoModel.por_id(vid)
    if not votacao or str(votacao.get("criador_id")) != current_user.id:
        flash("Sem permissão.", "danger")
        return redirect(url_for("votacoes.lista"))

    porta_coord = votacao.get("porta_coordenador")
    if porta_coord:
        encerrar_votacao(porta_coord)

    # Calcular resultado final
    candidatos = CandidatoModel.por_votacao(vid)
    contagem   = VotoModel.contagem_por_candidato(vid)
    resultado  = {str(c["_id"]): {"nome": c["nome"], "votos": contagem.get(str(c["_id"]), 0)}
                  for c in candidatos}
    VotacaoModel.encerrar(vid, resultado)

    flash("Votação encerrada com sucesso.", "success")
    return redirect(url_for("votacoes.detalhe", vid=vid))


# ---------------------------------------------------------------------------
# API: status em tempo real (consultada pelo dashboard via JS)
# ---------------------------------------------------------------------------
@votacoes_bp.route("/api/votacoes/<vid>/status")
def api_status(vid: str):
    votacao = VotacaoModel.por_id(vid)
    if not votacao:
        return jsonify({"error": "not found"}), 404

    porta_coord = votacao.get("porta_coordenador")
    status_coord = status_coordenador(porta_coord) if porta_coord else None

    urnas      = ServidorModel.urnas_por_votacao(vid)
    contadores = ServidorModel.contadores_por_votacao(vid)
    logs       = LogModel.recentes(vid, 10)
    contagem   = VotoModel.contagem_por_candidato(vid)
    candidatos = CandidatoModel.por_votacao(vid)

    # Enriquecer contagem com nomes
    resultado = []
    total = sum(contagem.values()) or 1
    for c in candidatos:
        cid   = str(c["_id"])
        votos = contagem.get(cid, 0)
        resultado.append({"nome": c["nome"], "votos": votos, "pct": round(votos/total*100,1)})

    def _fmt_dt(d):
        if hasattr(d, "isoformat"):
            return d.isoformat()
        return str(d)

    return jsonify({
        "votacao_id":   vid,
        "status":       votacao.get("status"),
        "total_votos":  votacao.get("total_votos", 0),
        "urnas": [{
            "id":             str(u["_id"]),
            "porta":          u["porta"],
            "status":         u["status"],
            "votos_recebidos": u.get("votos_recebidos", 0),
            "ultimo_heartbeat": _fmt_dt(u.get("ultimo_heartbeat")),
        } for u in urnas],
        "contadores": [{
            "id":     str(c["_id"]),
            "porta":  c["porta"],
            "status": c["status"],
            "ultimo_heartbeat": _fmt_dt(c.get("ultimo_heartbeat")),
        } for c in contadores],
        "resultado":    resultado,
        "logs": [{
            "tipo":      l["tipo"],
            "mensagem":  l["mensagem"],
            "timestamp": _fmt_dt(l["timestamp"]),
            "servidor":  l.get("servidor_id"),
        } for l in logs],
        "coord_status": status_coord,
    })
