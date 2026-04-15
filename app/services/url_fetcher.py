"""Serviço para buscar e extrair conteúdo de URLs para a base de conhecimento."""

import httpx
import re
import logging
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# Elementos HTML que não possuem tag de fechamento (void elements)
_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

# Tags que devem ser ignoradas completamente (conteúdo + filhos)
_SKIP_TAGS = frozenset({
    "script", "style", "nav", "footer", "header", "aside",
    "noscript", "iframe", "svg",
})


# ─────────────────────────────────────────────────────────────────────────────
# Parser genérico (fallback para qualquer URL)
# ─────────────────────────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Extrai texto limpo de HTML, ignorando scripts, estilos e tags de navegação."""

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.text_parts.append(text)


def _extract_text_from_html(html: str) -> str:
    """Extrai texto limpo de um HTML."""
    parser = _TextExtractor()
    parser.feed(html)
    text = "\n".join(parser.text_parts)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_title_from_html(html: str) -> str:
    """Tenta extrair o título da página."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Parser especializado — Páginas de cursos MBA da Impacta
# ─────────────────────────────────────────────────────────────────────────────

# Seções alvo na ordem em que aparecem na página
# (section_id_no_html, label_exibido_no_output)
_IMPACTA_SECTIONS = [
    ("sobrecurso",   "Sobre o Curso"),
    ("brxe-e1bf3f",  "Competências e Habilidades"),
    ("disciplinas",  "Programa Completo — Módulos e Disciplinas"),
]

# Tags cujo conteúdo não queremos coletar dentro das seções
_SECTION_SKIP_TAGS = _SKIP_TAGS | {"button"}


class _ImpactaCourseParser(HTMLParser):
    """
    Coleta texto estruturado das seções-chave de páginas de cursos MBA Impacta.

    Estratégia:
    - Rastreia profundidade do DOM (ignorando void elements).
    - Ao encontrar um elemento com id alvo, começa a coletar texto filhos.
    - Para de coletar quando o elemento fecha (mesma profundidade de abertura).
    - Ignora conteúdo dentro de _SECTION_SKIP_TAGS.
    """

    def __init__(self):
        super().__init__()
        self._target_ids = {sid: label for sid, label in _IMPACTA_SECTIONS}
        self.sections: dict[str, list[str]] = {sid: [] for sid, _ in _IMPACTA_SECTIONS}

        self._depth = 0
        # section_id -> profundidade em que a seção foi aberta
        self._active_sections: dict[str, int] = {}
        self._skip_depth = 0

    # ------------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        # Void elements não têm fechamento — não incrementa profundidade
        if tag in _VOID_ELEMENTS:
            return

        self._depth += 1

        if tag in _SECTION_SKIP_TAGS:
            self._skip_depth += 1
            return

        attrs_dict = dict(attrs)
        elem_id = attrs_dict.get("id", "")
        if elem_id in self._target_ids:
            self._active_sections[elem_id] = self._depth

    def handle_endtag(self, tag):
        if tag in _VOID_ELEMENTS:
            return

        if tag in _SECTION_SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        else:
            # Fecha seções que começaram nesta profundidade
            for sid in list(self._active_sections.keys()):
                if self._depth == self._active_sections[sid]:
                    del self._active_sections[sid]

        self._depth -= 1

    def handle_data(self, data):
        if self._skip_depth > 0 or not self._active_sections:
            return
        text = data.strip()
        if text:
            for sid in self._active_sections:
                self.sections[sid].append(text)

    # ------------------------------------------------------------------
    def get_content(self) -> str:
        """Formata as seções coletadas como texto estruturado."""
        parts = []
        for section_id, label in _IMPACTA_SECTIONS:
            texts = self.sections.get(section_id, [])
            if not texts:
                continue

            # Remove linhas consecutivas idênticas (artefatos de renderização)
            deduped: list[str] = []
            prev = None
            for t in texts:
                if t != prev:
                    deduped.append(t)
                    prev = t

            section_text = "\n".join(deduped)
            section_text = re.sub(r"\n{3,}", "\n\n", section_text).strip()
            parts.append(f"## {label}\n\n{section_text}")

        return "\n\n---\n\n".join(parts)

    def found_sections(self) -> list[str]:
        """Retorna os IDs das seções que tiveram conteúdo coletado."""
        return [sid for sid, texts in self.sections.items() if texts]


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de URL Impacta e extração
# ─────────────────────────────────────────────────────────────────────────────

def _is_impacta_course_url(url: str) -> bool:
    """Retorna True se a URL é de uma página de curso MBA da Impacta."""
    return bool(re.search(r"mba\.impacta\.edu\.br/(mbas|pos|cursos)/", url))


# ─────────────────────────────────────────────────────────────────────────────
# Parser especializado — Cursos Impacta (impacta.com.br/cursos/)
# ─────────────────────────────────────────────────────────────────────────────

def _is_impacta_cursos_url(url: str) -> bool:
    """Retorna True para páginas de cursos da Impacta (impacta.com.br/cursos/)."""
    return bool(re.search(r"impacta\.com\.br/cursos/", url))


# Seções que queremos extrair, em ordem de aparição na página
_IMPACTA_CURSOS_SECTIONS = [
    "Sobre o Curso",
    "Suas habilidades ao final do curso",
    "Este curso é para você que:",
    "Conteúdo programático",
    "Pré-requisitos",
]

# Marcadores que indicam fim do conteúdo útil
_IMPACTA_CURSOS_STOP = [
    "PRÓXIMAS TURMAS PRESENCIAIS",
    "PRÓXIMAS TURMAS ONLINE",
    "SOLICITE UMA COTAÇÃO",
    "O que os alunos dizem sobre",
    "nossos parceiros",
    "VOCÊ PRECISA DE MAIS INFORMAÇÕES",
    "novas features",
    "Maior escola de tecnologia",
    "CURSOS RELACIONADOS",
    "Conhecer todas as formações",
    "Download do conteúdo do curso",
    "Avise-me quando tiver novas datas",
    "ver mais datas",
]

# Linhas de ruído no cabeçalho (antes de "Sobre o Curso")
_HEADER_SKIP_RE = re.compile(
    r"^(Voltar|matricule-se|inscritos|Online ao Vivo|Presencial|Online e Presencial"
    r"|Teoria \+ [Pp]rática|ICS\s.*|Certificado\s.*"
    r"|CADASTRE-SE.*|Preencha seus dados.*|O curso é para.*"
    r"|Este curso (também )?está disponível.*|Este curso faz parte da"
    r"|Escola\s.*)$",
    re.IGNORECASE,
)
_HEADER_ALUNOS_RE = re.compile(r"^\+?\d[\d\.,]*\s*(alunos|mil\s*alunos)", re.IGNORECASE)

# Linhas curtas úteis no cabeçalho (carga horária, módulos)
_HEADER_KEEP_RE = re.compile(r"^\d+h de carga horária|^Módulo único$|^\d+ módulos?$", re.IGNORECASE)

# Noise dentro da seção "Sobre o Curso" (logo após a descrição)
_SOBRE_STOP_LINES = {"Projetos", "Reais", "Turbine seu portfólio", "Metodologia Hands-on", "Metodologia Hands-On"}
_SOBRE_RATING_RE  = re.compile(r"^\d+[\.,]\d+\s+Avalia")

# Noise dentro de "Pré-requisitos" (após o texto principal)
_PREREQ_STOP_RE = re.compile(r"^(Certificação Impacta|Download do conteúdo)", re.IGNORECASE)


def _extract_impacta_cursos_content(html: str, url: str) -> dict | None:
    """
    Parser especializado para páginas impacta.com.br/cursos/.
    Extrai: cabeçalho resumido, Sobre o Curso, Habilidades, Para quem,
    Conteúdo programático e Pré-requisitos. Remove todo o ruído da página.
    """
    title = _extract_title_from_html(html)
    raw   = _extract_text_from_html(html)
    if not raw:
        return None

    lines = [l.strip() for l in raw.split("\n")]

    def find_section(marker: str) -> int:
        """Busca linha que seja exatamente o marcador OU que o contenha como trecho único."""
        m = marker.lower()
        for i, l in enumerate(lines):
            ll = l.lower()
            if ll == m:
                return i
            # aceita linha levemente maior (ex: marcador + pontuação)
            if ll.startswith(m) and len(l) <= len(marker) + 5:
                return i
            # aceita linha que contenha o marcador inteiro se for curta
            if m in ll and len(l) <= len(marker) + 10:
                return i
        return -1

    def find_stop(from_line: int = 0) -> int:
        """Localiza primeiro marcador de parada a partir de from_line."""
        for i in range(from_line, len(lines)):
            for stop in _IMPACTA_CURSOS_STOP:
                if stop.lower() in lines[i].lower():
                    return i
        return len(lines)

    # A página precisa ter "Sobre o Curso" para usar este parser
    sobre_pos = find_section("Sobre o Curso")
    if sobre_pos == -1:
        logger.warning("Parser Impacta Cursos: 'Sobre o Curso' não encontrado em %s", url)
        return None

    # ── Cabeçalho: extrai tagline e info básica antes de "Sobre o Curso" ──
    header_parts = []
    for line in lines[:sobre_pos]:
        if not line:
            continue
        if _HEADER_SKIP_RE.match(line) or _HEADER_ALUNOS_RE.match(line):
            continue
        if _HEADER_KEEP_RE.match(line):   # carga horária, módulos
            header_parts.append(line)
        elif len(line) > 45:              # tagline longa
            header_parts.append(line)

    # ── Localiza seções encontradas ───────────────────────────────────────
    sec_positions = []
    for marker in _IMPACTA_CURSOS_SECTIONS:
        pos = find_section(marker)
        if pos != -1:
            sec_positions.append((pos, marker))
        else:
            logger.debug("Parser Impacta Cursos: seção '%s' não encontrada", marker)
    sec_positions.sort(key=lambda x: x[0])

    # stop a partir da última seção encontrada
    last_sec_pos = sec_positions[-1][0] if sec_positions else sobre_pos
    stop_pos = find_stop(from_line=last_sec_pos)

    logger.info(
        "Parser Impacta Cursos: %d seções encontradas, stop_pos=%d, total_lines=%d | %s",
        len(sec_positions), stop_pos, len(lines), url,
    )

    # ── Extrai conteúdo de cada seção ─────────────────────────────────────
    section_texts = []
    for idx, (pos, marker) in enumerate(sec_positions):
        end = sec_positions[idx + 1][0] if idx + 1 < len(sec_positions) else stop_pos

        sec_lines = lines[pos:end]

        if marker == "Sobre o Curso":
            clean = []
            for l in sec_lines:
                if _SOBRE_RATING_RE.match(l) or l in _SOBRE_STOP_LINES:
                    break
                clean.append(l)
            sec_lines = clean

        if marker == "Pré-requisitos":
            clean = []
            for l in sec_lines:
                if _PREREQ_STOP_RE.match(l):
                    break
                clean.append(l)
            sec_lines = clean

        text = "\n".join(l for l in sec_lines if l)
        if text:
            section_texts.append(text)

    if not section_texts:
        logger.warning("Parser Impacta Cursos: nenhuma seção com conteúdo extraída de %s", url)
        return None

    parts = []
    if header_parts:
        parts.append("\n".join(header_parts))
    parts.extend(section_texts)

    content = f"Fonte: {url}\n\n" + "\n\n".join(parts)

    max_chars = 20_000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[Conteúdo truncado — {len(content)} caracteres originais]"

    logger.info("Parser Impacta Cursos: conteúdo final %d chars | %s", len(content), url)
    return {"success": True, "title": title or url, "content": content}


def _extract_impacta_content(html: str, url: str) -> dict | None:
    """
    Tenta extrair conteúdo estruturado de uma página de curso Impacta.
    Retorna None se nenhuma seção for encontrada (sinal para usar fallback genérico).
    """
    parser = _ImpactaCourseParser()
    parser.feed(html)

    found = parser.found_sections()
    if not found:
        logger.warning("Parser Impacta: nenhuma seção encontrada em %s — usando extração genérica.", url)
        return None

    logger.info("Parser Impacta: seções encontradas em %s: %s", url, found)
    content = parser.get_content()
    title = _extract_title_from_html(html)

    return {
        "success": True,
        "title": title or url,
        "content": f"Fonte: {url}\n\n{content}",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Função pública
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_url_content(url: str) -> dict:
    """
    Busca o conteúdo de uma URL e extrai o texto.

    Para páginas de cursos MBA da Impacta (mba.impacta.edu.br/mbas/...),
    extrai automaticamente:
      - Sobre o Curso
      - Competências e Habilidades
      - Programa Completo (módulos, disciplinas, carga horária)

    Para outras URLs usa extração genérica de texto.

    Returns:
        {
            "success": bool,
            "title": str,
            "content": str,
            "error": str  # presente apenas em caso de falha
        }
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        }

        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return {
                    "success": False,
                    "error": (
                        f"Tipo de conteúdo não suportado: {content_type}. "
                        "Só é possível importar páginas HTML."
                    ),
                }

            html = response.text

            # ── Parser especializado: impacta.com.br/cursos/ ─────────────
            if _is_impacta_cursos_url(url):
                result = _extract_impacta_cursos_content(html, url)
                if result:
                    return result

            # ── Parser especializado: mba.impacta.edu.br ─────────────────
            if _is_impacta_course_url(url):
                result = _extract_impacta_content(html, url)
                if result:
                    max_chars = 15_000
                    if len(result["content"]) > max_chars:
                        result["content"] = (
                            result["content"][:max_chars]
                            + f"\n\n[Conteúdo truncado — {len(result['content'])} caracteres originais]"
                        )
                    return result

            # ── Fallback: extração genérica ─────────────────────────────
            title = _extract_title_from_html(html)
            text = _extract_text_from_html(html)

            max_chars = 20_000
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n[Conteúdo truncado — {len(text)} caracteres originais]"

            if not text:
                return {"success": False, "error": "Não foi possível extrair texto da página."}

            return {
                "success": True,
                "title": title or url,
                "content": f"Fonte: {url}\n\n{text}",
            }

    except httpx.TimeoutException:
        return {"success": False, "error": "Tempo limite excedido ao acessar a URL."}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"Erro HTTP {e.response.status_code} ao acessar a URL."}
    except Exception as e:
        logger.error("Erro ao buscar URL %s: %s", url, e)
        return {"success": False, "error": f"Erro ao acessar a URL: {str(e)}"}
