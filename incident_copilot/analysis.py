"""Normalização e análise conservadora de notificações do Alertmanager."""

from __future__ import annotations

import re
from typing import Any

MAX_ALERTS = 20
MAX_FIELD = 500
ALLOWED_LABELS = ("alertname", "severity", "service", "namespace", "pod", "job")
SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[a-z0-9._~+/-]+"),
    re.compile(r"(?i)\b((?:password|passwd|token|api[_-]?key|secret)\s*[:=]\s*)\S+"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)


def clean(value: Any) -> str:
    """Reduz o campo e remove formas comuns de segredo antes de exibi-lo ou enviá-lo ao LLM."""
    result = str(value if isinstance(value, (str, int, float)) else "")[:MAX_FIELD]
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", result)
    return result


def analyze(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise ValueError("Payload inválido: esperado objeto com lista 'alerts'.")
    if not payload["alerts"] or len(payload["alerts"]) > MAX_ALERTS:
        raise ValueError(f"A notificação deve conter de 1 a {MAX_ALERTS} alertas.")

    results = []
    for alert in payload["alerts"]:
        if not isinstance(alert, dict):
            raise ValueError("Cada alerta precisa ser um objeto.")
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        if not isinstance(labels, dict) or not isinstance(annotations, dict):
            raise ValueError("Labels e annotations devem ser objetos.")
        safe_labels = {key: clean(labels[key]) for key in ALLOWED_LABELS if key in labels}
        summary = clean(annotations.get("summary", ""))
        description = clean(annotations.get("description", ""))
        evidence = " ".join((" ".join(safe_labels.values()), summary, description)).lower()
        hypotheses, checks = _suggest(evidence, safe_labels)
        results.append({
            "status": "resolved" if alert.get("status") == "resolved" else "firing",
            "labels": safe_labels,
            "summary": summary,
            "description": description,
            "hypotheses": hypotheses,
            "checks": checks,
        })

    return {
        "source": "Alertmanager webhook",
        "group_status": "resolved" if payload.get("status") == "resolved" else "firing",
        "alerts": results,
        "note": "Hipóteses para investigação; não são diagnóstico nem autorização para remediação.",
    }


def _suggest(evidence: str, labels: dict[str, str]) -> tuple[list[str], list[str]]:
    namespace = labels.get("namespace", "<namespace>")
    pod = labels.get("pod", "<pod>")
    # Só interpolamos nomes Kubernetes com um alfabeto limitado para evitar
    # apresentar comandos perigosos a partir de dados não confiáveis.
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", namespace):
        namespace = "<namespace>"
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", pod):
        pod = "<pod>"
    checks = ["Conferir o impacto no serviço e a janela do alerta."]
    hypotheses = []
    if "oom" in evidence or "memory" in evidence or "memória" in evidence:
        hypotheses.append("Possível esgotamento de memória; confirmar limite e consumo antes de alterar recursos.")
        checks.append(f"kubectl describe pod {pod} -n {namespace}")
        checks.append("Comparar uso de memória e reinícios no painel de métricas.")
    if "crashloop" in evidence or "restart" in evidence or "reinício" in evidence:
        hypotheses.append("O processo pode estar encerrando após iniciar; verificar motivo, eventos e logs anteriores.")
        checks.append(f"kubectl logs {pod} -n {namespace} --previous --tail=100")
        checks.append(f"kubectl get events -n {namespace} --sort-by=.lastTimestamp")
    if "dns" in evidence or "name resolution" in evidence:
        hypotheses.append("Falha de resolução de nomes ou dependência inacessível; verificar escopo do problema.")
        checks.append("Conferir registros DNS, CoreDNS e conectividade do serviço afetado.")
    if "5xx" in evidence or "http 500" in evidence or "http 503" in evidence:
        hypotheses.append("Erros no serviço ou na dependência; correlacionar taxa de erro, latência e logs.")
        checks.append("Conferir taxa de erro e latência antes e depois do início do alerta.")
    if not hypotheses:
        hypotheses.append("Causa ainda indeterminada; reunir métricas, logs e mudanças recentes.")
        checks.append("Revisar deploys, eventos e dashboards no horário do alerta.")
    # Evita instruções duplicadas quando um alerta traz mais de um sintoma.
    return hypotheses, list(dict.fromkeys(checks))


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Triagem de incidente", "", report["note"], ""]
    for index, alert in enumerate(report["alerts"], 1):
        labels = alert["labels"]
        lines += [f"## {index}. {labels.get('alertname', 'Alerta sem nome')} ({alert['status']})", ""]
        lines += [f"**Serviço:** {labels.get('service', 'não informado')}  ", f"**Resumo:** {alert['summary'] or 'não informado'}", ""]
        lines += ["**Hipóteses a confirmar**", ""] + [f"- {item}" for item in alert["hypotheses"]]
        lines += ["", "**Próximas verificações**", ""] + [f"- `{item}`" if item.startswith("kubectl ") else f"- {item}" for item in alert["checks"]]
        lines.append("")
    if report.get("ai_suggestion"):
        lines += ["## Sugestão do modelo local (não verificada)", "", report["ai_suggestion"], ""]
    if report.get("ai_error"):
        lines += ["## Modelo local", "", report["ai_error"], ""]
    return "\n".join(lines)

