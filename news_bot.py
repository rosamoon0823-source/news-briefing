# -*- coding: utf-8 -*-
"""
뉴스 브리핑 봇 (매일 아침 실행)

파이프라인:
  1) 수집   : config.py의 RSS 소스에서 최근 기사 수집
  2) 선별   : Claude가 중복 기사를 묶고, 여러 매체가 보도한 굵직한 이슈만 선별
  3) 본문   : 선별된 기사만 본문 추출 (trafilatura)
  4) 요약   : Claude가 이슈별 요약 + 전체 개요 + 카톡용 짧은 텍스트 생성
  5) 발행   : docs/ 폴더에 브리핑 HTML 생성 (GitHub Pages로 공개됨)
  6) 발송   : 카카오톡 '나에게 보내기'로 헤드라인 + 링크 전송

필요한 환경변수:
  ANTHROPIC_API_KEY   : Claude API 키
  KAKAO_REST_API_KEY  : 카카오 앱 REST API 키
  KAKAO_REFRESH_TOKEN : 카카오 리프레시 토큰 (get_kakao_token.py로 최초 발급)
  PAGES_BASE_URL      : 브리핑 페이지 주소 (예: https://아이디.github.io/저장소명)
"""

import os
import re
import json
import html
import hashlib
import datetime
import traceback

import requests
import feedparser
import trafilatura
from anthropic import Anthropic

from config import (
    SITE_TITLE, TOTAL_MAX, MAX_PER_CATEGORY, MIN_PER_CATEGORY, COLLECT_HOURS,
    CLAUDE_MODEL, CATEGORIES, CATEGORY_HINTS, NAVER_QUERIES,
    KAKAO_CHAR_LIMIT, KAKAO_CAT_LABELS,
)

KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
TODAY = NOW.strftime("%Y-%m-%d")
TODAY_KR = NOW.strftime("%Y년 %m월 %d일") + " " + "월화수목금토일"[NOW.weekday()] + "요일"
# 카톡 1통은 200자 제한이 빡빡해 연도를 뺀 짧은 날짜를 쓴다
TODAY_KR_SHORT = NOW.strftime("%m월 %d일") + " " + "월화수목금토일"[NOW.weekday()] + "요일"

# 하루 2회 실행 구분 (정오 이전 = 아침 브리핑, 이후 = 저녁 브리핑)
IS_MORNING = NOW.hour < 12
RUN_LABEL = "아침" if IS_MORNING else "저녁"
RUN_EMOJI = "☀️" if IS_MORNING else "🌙"
RUN_SLUG = "am" if IS_MORNING else "pm"

SENT_LOG_PATH = "docs/sent_log.json"


def load_sent_log():
    """최근 3일간 발송한 이슈 제목 목록 (아침/저녁 중복 방지용)"""
    try:
        with open(SENT_LOG_PATH, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return []
    cutoff = (NOW - datetime.timedelta(days=3)).strftime("%Y-%m-%d")
    return [e for e in log if e.get("date", "") >= cutoff]


def save_sent_log(log, headlines):
    log = log + [{"date": TODAY, "headline": h} for h in headlines]
    os.makedirs("docs", exist_ok=True)
    with open(SENT_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# 1) 수집
# ---------------------------------------------------------------------------

def _norm_title(t: str) -> str:
    """제목 비교용 정규화 (공백/기호 제거)"""
    t = re.sub(r"\[.*?\]|\(.*?\)|【.*?】", " ", t)
    t = re.sub(r"[^\w가-힣]", "", t)
    return t.lower()[:40]


def collect_articles():
    cutoff = NOW - datetime.timedelta(hours=COLLECT_HOURS)
    articles = []
    seen = set()
    dead_feeds = []

    for category, feeds in CATEGORIES.items():
        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url, request_headers={
                    "User-Agent": "Mozilla/5.0 (news-briefing-bot)"
                })
                entries = parsed.entries
                if not entries:
                    dead_feeds.append(feed_url)
                    continue
            except Exception:
                dead_feeds.append(feed_url)
                continue

            for e in entries[:40]:
                title = html.unescape(getattr(e, "title", "")).strip()
                link = getattr(e, "link", "")
                if not title or not link:
                    continue

                # 발행 시각 확인 (없으면 포함시키되 나중 순위)
                published = None
                for key in ("published_parsed", "updated_parsed"):
                    tp = getattr(e, key, None)
                    if tp:
                        published = datetime.datetime(*tp[:6], tzinfo=datetime.timezone.utc).astimezone(KST)
                        break
                if published and published < cutoff:
                    continue

                # Google News 제목 뒤의 " - 언론사명" 분리
                source = ""
                m = re.search(r"^(.*)\s-\s([^-]{2,20})$", title)
                if "news.google.com" in feed_url and m:
                    title, source = m.group(1).strip(), m.group(2).strip()
                if not source:
                    source = getattr(getattr(e, "source", None), "title", "") or \
                             parsed.feed.get("title", "").split("-")[0].strip()

                key = _norm_title(title)
                if key in seen:
                    continue
                seen.add(key)

                articles.append({
                    "id": len(articles),
                    "category": category,
                    "title": title,
                    "source": source[:20],
                    "link": link,
                    "published": published.strftime("%m-%d %H:%M") if published else "",
                })

    if dead_feeds:
        print(f"[알림] 응답 없는 피드 {len(dead_feeds)}개 (config.py에서 교체 권장):")
        for f in dead_feeds:
            print("   -", f)

    _collect_naver(articles, seen)
    print(f"[수집] 총 {len(articles)}건")
    return articles


def _collect_naver(articles, seen):
    """네이버 뉴스 검색 API 수집. 키가 없으면 조용히 건너뜀 (선택 기능)."""
    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        print("[네이버] API 키 미설정 — 네이버 수집은 건너뜁니다.")
        return

    import email.utils
    from urllib.parse import urlparse
    cutoff = NOW - datetime.timedelta(hours=COLLECT_HOURS)
    added = 0

    for category, query in NAVER_QUERIES.items():
        try:
            resp = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": query, "display": 30, "sort": "date"},
                headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            print(f"[네이버] '{query}' 검색 실패: {e}")
            continue

        for item in items:
            title = html.unescape(re.sub(r"</?b>", "", item.get("title", ""))).strip()
            link = item.get("originallink") or item.get("link", "")
            if not title or not link:
                continue
            try:
                pub = email.utils.parsedate_to_datetime(item["pubDate"]).astimezone(KST)
                if pub < cutoff:
                    continue
                published = pub.strftime("%m-%d %H:%M")
            except Exception:
                published = ""

            key = _norm_title(title)
            if key in seen:
                continue
            seen.add(key)

            source = urlparse(link).netloc.replace("www.", "").split(".")[0]
            articles.append({
                "id": len(articles),
                "category": category,
                "title": title,
                "source": source[:20],
                "link": link,
                "published": published,
            })
            added += 1

    print(f"[네이버] {added}건 추가 수집")


# ---------------------------------------------------------------------------
# 2) 선별 (Claude 1차 호출)
# ---------------------------------------------------------------------------

# 모델 응답 JSON 스키마.
# output_config로 출력 형식을 강제하므로 모델이 형식을 어길 수 없다.
SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "headline": {"type": "string"},
                    "article_ids": {"type": "array", "items": {"type": "integer"}},
                    "importance": {"type": "integer"},
                },
                "required": ["category", "headline", "article_ids", "importance"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["clusters"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string"},
        "kakao_lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster": {"type": "integer"},
                    "line": {"type": "string"},
                },
                "required": ["cluster", "line"],
                "additionalProperties": False,
            },
        },
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster": {"type": "integer"},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["cluster", "headline", "summary", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "kakao_lines", "sections"],
    "additionalProperties": False,
}

# 응답 토큰 상한. 한국어는 토큰 소모가 커서 넉넉히 잡는다.
# 상한일 뿐이므로 실제 생성한 토큰만 과금된다 (올려도 비용은 늘지 않는다).
MAX_TOKENS = 16000


def _call_json(client: Anthropic, prompt: str, schema: dict):
    """Claude를 호출해 스키마에 맞는 JSON을 받아 dict로 반환한다.

    - output_config로 형식을 강제하므로 응답은 항상 유효한 JSON이다.
    - max_tokens에 걸려 잘린 경우는 조용히 넘기지 않고 즉시 실패시킨다.
      (잘린 JSON을 그대로 파싱하면 원인을 알 수 없는 JSONDecodeError가 된다)
    """
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    if resp.stop_reason == "max_tokens":
        raise RuntimeError(
            "모델 응답이 max_tokens(%d)에 걸려 잘렸습니다. "
            "config.py의 TOTAL_MAX를 줄이거나 MAX_TOKENS를 올리세요." % MAX_TOKENS
        )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def _pick_balanced(clusters):
    """카테고리별 최소·최대 개수를 보장하며 최종 이슈를 고른다.

    모델이 프롬프트 규칙을 어기는 경우가 있어(카테고리 상한 초과, 특정 분야 누락)
    코드에서 한 번 더 강제한다.

    1단계: 모든 카테고리에서 중요도 상위 MIN_PER_CATEGORY개를 먼저 확보한다.
           분야 커버리지가 목적이므로 이 단계에서는 중요도가 낮아도 채운다.
    2단계: 남은 자리를 전체 중요도 순으로 채운다 (카테고리당 MAX_PER_CATEGORY 상한).
    """
    by_cat = {}
    for c in clusters:
        by_cat.setdefault(c.get("category", "기타"), []).append(c)
    for lst in by_cat.values():
        lst.sort(key=lambda c: -c.get("importance", 5))

    # config.py의 카테고리 순서를 따르고, 목록에 없는 카테고리는 뒤에 붙인다
    order = [c for c in CATEGORIES if c in by_cat] +             [c for c in by_cat if c not in CATEGORIES]

    picked, taken, counts = [], set(), {}

    for cat in order:                                    # 1단계: 필수 커버리지
        for c in by_cat[cat][:MIN_PER_CATEGORY]:
            picked.append(c)
            taken.add(id(c))
            counts[cat] = counts.get(cat, 0) + 1

    for c in sorted(clusters, key=lambda c: -c.get("importance", 5)):   # 2단계
        if len(picked) >= TOTAL_MAX:
            break
        if id(c) in taken:
            continue
        cat = c.get("category", "기타")
        if counts.get(cat, 0) >= MAX_PER_CATEGORY:
            continue
        picked.append(c)
        taken.add(id(c))
        counts[cat] = counts.get(cat, 0) + 1

    picked.sort(key=lambda c: -c.get("importance", 5))
    missing = [c for c in CATEGORIES if c not in by_cat]
    return picked, counts, missing


def select_clusters(client: Anthropic, articles, sent_log):
    lines = [f'{a["id"]}|{a["category"]}|{a["source"]}|{a["title"]}' for a in articles]
    hints = "\n".join(f"- {c}: {h}" for c, h in CATEGORY_HINTS.items())
    already = "\n".join(f'- {e["headline"]}' for e in sent_log[-40:]) or "(없음)"

    prompt = f"""너는 바쁜 직장인을 위한 뉴스 큐레이터다. 오늘({TODAY_KR}) {RUN_LABEL} 브리핑에 담을 이슈를 고른다.

## 기사 목록 (형식: id|카테고리|매체|제목)
{chr(10).join(lines)}

## 이미 지난 브리핑에서 다룬 이슈 (중요한 새 전개가 없는 한 다시 고르지 말 것)
{already}

## 선별 규칙 (매우 중요)
1. 같은 사건을 다룬 기사들은 하나의 '이슈'로 묶는다.
2. 자잘한 단신, 홍보성 기사, 개별 사건사고, 시황 중계, 연예 가십은 제외한다.
3. **모든 카테고리에서 최소 {MIN_PER_CATEGORY}개, 최대 {MAX_PER_CATEGORY}개**를 고른다 (전체 최대 {TOTAL_MAX}개). 독자가 매일 모든 분야를 훑을 수 있어야 하므로, 뉴스가 빈약한 카테고리에서도 그날 가장 나은 이슈를 반드시 1개는 고른다. 대신 그런 이슈에는 importance를 낮게 매겨라.
4. 카테고리별 관점:
{hints}
5. 한 이슈의 article_ids에는 대표 기사를 첫 번째로, 같은 사건의 다른 기사를 최대 2개까지 추가한다.

## importance 점수 산정 기준 (1~10)
- 보도 폭 (40%): 몇 개 매체가 같은 사건을 다뤘는가. 다매체 보도 = 강한 중요도 신호.
- 파급 범위 (30%): 얼마나 많은 사람/산업에 영향을 주는가.
- 독자 관련성 (20%): 광고회사 서비스 기획자의 업무·커리어와 얼마나 맞닿아 있는가.
- 시의성 (10%): 지금 알아야 하는가, 나중에 알아도 되는가.
- importance 5 미만인 이슈는 넣지 않는다. 단 3번 규칙(분야별 최소 개수)이 우선이므로, 5점 이상이 없는 카테고리에서는 가장 나은 이슈를 실제 점수 그대로(낮게) 넣는다.

## 출력 형식
아래 JSON만 출력한다. 다른 텍스트 금지.
{{"clusters": [{{"category": "카테고리명", "headline": "이슈를 요약한 한 줄 제목", "article_ids": [대표id, ...], "importance": 1~10}}]}}"""

    data = _call_json(client, prompt, SELECT_SCHEMA)
    clusters, counts, missing = _pick_balanced(data["clusters"])
    dist = ", ".join(f"{cat} {n}" for cat, n in counts.items())
    print(f"[선별] {len(clusters)}개 이슈 선정 ({dist})")
    if missing:
        print(f"[선별] 기사가 없어 채우지 못한 분야: {', '.join(missing)}")
    return clusters


# ---------------------------------------------------------------------------
# 3) 본문 추출
# ---------------------------------------------------------------------------

def fetch_article_text(url: str) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False) or ""
            return text[:3500]
    except Exception:
        pass
    return ""


def attach_texts(clusters, articles):
    by_id = {a["id"]: a for a in articles}
    for c in clusters:
        c["articles"] = [by_id[i] for i in c["article_ids"] if i in by_id]
        c["text"] = ""
        for a in c["articles"]:
            body = fetch_article_text(a["link"])
            if len(body) > 300:
                c["text"] = body
                break
        if not c["text"]:  # 본문 추출 실패 시 제목들로 대체
            c["text"] = " / ".join(a["title"] for a in c["articles"])
    return clusters


# ---------------------------------------------------------------------------
# 4) 요약 (Claude 2차 호출)
# ---------------------------------------------------------------------------

def summarize(client: Anthropic, clusters):
    blocks = []
    for i, c in enumerate(clusters):
        srcs = ", ".join(dict.fromkeys(a["source"] for a in c["articles"] if a["source"]))
        blocks.append(
            f"### 이슈 {i} [{c['category']}] {c['headline']}\n보도 매체: {srcs}\n본문 발췌:\n{c['text']}"
        )

    prompt = f"""너는 신뢰받는 아침 뉴스 브리핑의 에디터다. 아래 선별된 이슈들로 {TODAY_KR} 브리핑을 작성한다.
독자는 광고회사에서 서비스 기획을 하는 직장인이다.

{chr(10).join(blocks)}

## 작성 규칙
- summary: 이슈당 3~4문장. 사실 위주, 담백하고 명확하게. 본문에 없는 내용을 지어내지 않는다.
- why: "그래서 이게 왜 중요한가"를 독자 관점에서 한 문장으로.
- overview: 브리핑 맨 위에 들어갈 오늘의 흐름 요약 2~3문장.
- kakao_lines: 카카오톡 알림용. 각 이슈마다 {{"cluster": 이슈번호, "line": "15자 이내 압축 제목"}} 형태로 전부 담는다. 카톡 1통(200자)에 8개 분야가 들어가야 하므로 15자를 넘기면 잘린다. 조사·수식어를 버리고 핵심 명사만 남겨라.
- 영어 기사도 모두 한국어로 요약한다.

## 쉬운 언어 규칙 (모든 텍스트에 적용, 매우 중요)
뉴스 배경지식이 없는 사람이 검색 없이 한 번에 이해할 수 있어야 한다.
- 전문 용어는 일상어로 풀어 쓴다. 불가피하면 괄호로 한 줄 설명을 붙인다.
  예: "양적 긴축" → "시중에 풀린 돈을 거둬들이는 정책", "콜옵션 행사" → "미리 정한 가격에 살 수 있는 권리를 행사"
- 약어·영문 축약어는 첫 등장 시 풀어 쓴다. 예: "중앙은행이 발행하는 디지털 화폐(CBDC)"
- 큰 숫자는 체감되는 비교를 붙인다. 예: "3조 원 적자(전년의 두 배 수준)"
- "무엇이 → 왜 → 그래서 어떻게 되는지" 순서로 쓴다. 한 문장에 하나의 정보만 담는다.
- 예외: AI 동향·광고업계·서비스 기획 카테고리에서는 실무자에게 통용되는 용어(예: 타겟팅, 리텐션, 온보딩)는 풀지 않고 그대로 쓴다.
- 검수 기준: 읽다가 검색창을 열고 싶어지는 단어가 있으면 실패다.

## 출력 형식
아래 JSON만 출력한다. 다른 텍스트 금지.
{{"overview": "...", "kakao_lines": [{{"cluster": 이슈번호, "line": "..."}}], "sections": [{{"cluster": 이슈번호, "headline": "다듬은 제목", "summary": "...", "why": "..."}}]}}"""

    data = _call_json(client, prompt, SUMMARY_SCHEMA)
    print("[요약] 완료")
    return data


# ---------------------------------------------------------------------------
# 5) 브리핑 페이지 생성 (GitHub Pages)
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} · {date}</title>
<link rel="stylesheet" as="style" crossorigin
 href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<style>
:root {{
  --ink: #191d24; --sub: #5c6470; --line: #e8e6e1;
  --paper: #fbfaf7; --card: #ffffff; --accent: #16565c; --accent-soft: #e3efee;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: "Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo",
               "Malgun Gothic", sans-serif;
  background: var(--paper); color: var(--ink);
  line-height: 1.65; -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 640px; margin: 0 auto; padding: 28px 20px 60px; }}
header {{ padding: 8px 0 20px; border-bottom: 2px solid var(--ink); }}
.brand {{ font-size: 13px; font-weight: 700; letter-spacing: .14em; color: var(--accent); }}
h1 {{ font-size: 26px; font-weight: 800; letter-spacing: -0.02em; margin-top: 6px; }}
.count {{ font-size: 13px; color: var(--sub); margin-top: 4px; }}
.overview {{
  margin: 20px 0 8px; padding: 16px 18px; background: var(--accent-soft);
  border-radius: 12px; font-size: 15px; color: #123d42;
}}
.cat-label {{
  margin: 34px 0 2px; font-size: 12.5px; font-weight: 800;
  letter-spacing: .12em; color: var(--accent);
  display: flex; align-items: center; gap: 10px;
}}
.cat-label::after {{ content: ""; flex: 1; height: 1px; background: var(--line); }}
article {{
  background: var(--card); border: 1px solid var(--line);
  border-radius: 14px; padding: 18px 18px 14px; margin-top: 12px;
}}
.imp {{
  display: inline-block; font-size: 11.5px; font-weight: 800;
  padding: 3px 10px; border-radius: 20px; margin-bottom: 8px; letter-spacing: .02em;
}}
.imp-core  {{ background: #fdeae7; color: #b93a2b; }}
.imp-major {{ background: #fdf3e3; color: #b07515; }}
.imp-minor {{ background: #eef0f2; color: #5c6470; }}
h2 {{ font-size: 17.5px; font-weight: 750; letter-spacing: -0.01em; line-height: 1.4; }}
.summary {{ font-size: 15px; margin-top: 10px; }}
.why {{
  margin-top: 12px; padding-left: 12px; border-left: 3px solid var(--accent);
  font-size: 14px; color: var(--sub);
}}
.links {{ margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--line); font-size: 13px; }}
.links a {{ color: var(--accent); text-decoration: none; margin-right: 14px; }}
.links a:hover {{ text-decoration: underline; }}
footer {{ margin-top: 44px; font-size: 12.5px; color: var(--sub); text-align: center; }}
footer a {{ color: var(--sub); }}
@media (prefers-reduced-motion: no-preference) {{
  article {{ animation: rise .4s ease both; }}
  @keyframes rise {{ from {{ opacity: 0; transform: translateY(6px); }} }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="brand">DAILY BRIEFING</div>
    <h1>{title}</h1>
    <div class="count">{date} · 오늘의 이슈 {n}건</div>
  </header>
  <div class="overview">{overview}</div>
  {body}
  <footer>모든 요약은 원문 기사를 바탕으로 AI가 작성했습니다 · 정확한 내용은 원문 링크를 확인하세요<br>
  <a href="./archive.html">지난 브리핑 보기</a></footer>
</div>
</body>
</html>"""


def _imp_label(imp):
    """내부 점수(1~10) → 사용자용 3단계 라벨"""
    if imp >= 9:
        return "🔴 핵심", "imp-core"
    if imp >= 7:
        return "🟠 주요", "imp-major"
    return "🟡 참고", "imp-minor"


def render_page(clusters, summary_data):
    sec_by_cluster = {s["cluster"]: s for s in summary_data["sections"]}

    # 카테고리는 config 순서대로, 카테고리 안에서는 중요도 내림차순으로 정렬
    by_cat = {}
    for i, c in enumerate(clusters):
        by_cat.setdefault(c["category"], []).append((i, c))
    cat_order = [c for c in CATEGORIES if c in by_cat] + \
                [c for c in by_cat if c not in CATEGORIES]

    body_parts = []
    n = 0
    for cat in cat_order:
        items = sorted(by_cat[cat], key=lambda x: -x[1].get("importance", 5))
        cat_rendered = False
        for i, c in items:
            s = sec_by_cluster.get(i)
            if not s:
                continue
            if not cat_rendered:
                body_parts.append(f'<div class="cat-label">{html.escape(cat)}</div>')
                cat_rendered = True
            n += 1
            label, css = _imp_label(c.get("importance", 5))
            links = "".join(
                f'<a href="{html.escape(a["link"])}" target="_blank" rel="noopener">'
                f'{html.escape(a["source"] or "원문")} ↗</a>'
                for a in c["articles"][:3]
            )
            body_parts.append(f"""<article>
  <span class="imp {css}">{label}</span>
  <h2>{html.escape(s["headline"])}</h2>
  <p class="summary">{html.escape(s["summary"])}</p>
  <p class="why">{html.escape(s["why"])}</p>
  <div class="links">{links}</div>
</article>""")

    page = PAGE_TEMPLATE.format(
        title=f"{SITE_TITLE} · {RUN_LABEL}", date=TODAY_KR, n=n,
        overview=html.escape(summary_data["overview"]),
        body="\n".join(body_parts),
    )

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(page)
    with open(f"docs/{TODAY}-{RUN_SLUG}.html", "w", encoding="utf-8") as f:
        f.write(page)
    _update_archive()
    print("[발행] docs/index.html 생성 완료")
    return n


def _update_archive():
    dates = sorted(
        (f[:-5] for f in os.listdir("docs")
         if re.fullmatch(r"\d{4}-\d{2}-\d{2}(-am|-pm)?\.html", f)),
        reverse=True,
    )
    label = {"am": " 아침", "pm": " 저녁"}
    items = "\n".join(
        f'<li><a href="./{d}.html">{d[:10]}{label.get(d[11:], "")}</a></li>' for d in dates
    )
    with open("docs/archive.html", "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>지난 브리핑</title>
<style>body{{font-family:Pretendard,-apple-system,sans-serif;max-width:640px;margin:0 auto;
padding:40px 20px;line-height:2}}a{{color:#16565c}}</style></head>
<body><h1>지난 브리핑</h1><ul>{items}</ul></body></html>""")


# ---------------------------------------------------------------------------
# 6) 카카오톡 발송
# ---------------------------------------------------------------------------

def kakao_get_access_token():
    """리프레시 토큰으로 액세스 토큰 발급. 새 리프레시 토큰이 오면 파일로 저장
    (GitHub Actions가 이 파일을 읽어 Secret을 자동 갱신함)."""
    data = {
        "grant_type": "refresh_token",
        "client_id": os.environ["KAKAO_REST_API_KEY"],
        "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
    }
    secret = os.environ.get("KAKAO_CLIENT_SECRET")
    if secret:  # 클라이언트 시크릿 사용 시에만 포함 (미사용이면 자동 생략)
        data["client_secret"] = secret
    resp = requests.post("https://kauth.kakao.com/oauth/token", data=data, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "refresh_token" in data:
        with open("new_refresh_token.txt", "w") as f:
            f.write(data["refresh_token"])
        print("[카카오] 리프레시 토큰이 갱신되어 저장했습니다.")
    return data["access_token"]


def kakao_send(access_token: str, text: str, url: str, button="브리핑 전체 보기"):
    template = {
        "object_type": "text",
        "text": text[:KAKAO_CHAR_LIMIT],
        "link": {"web_url": url, "mobile_web_url": url},
        "button_title": button,
    }
    resp = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    resp.raise_for_status()


def build_kakao_messages(kakao_lines, clusters, n):
    """분야별 대표 이슈 1건씩을 카톡 1통(200자)에 담는다.

    카카오 텍스트 템플릿은 200자가 상한이어서 15건을 모두 담을 수 없다.
    그래서 매일 같은 자리에서 같은 분야를 읽을 수 있도록 CATEGORIES 순서대로
    분야별 1건씩만 싣고, 나머지는 브리핑 페이지로 넘긴다.

    - 분야별 대표는 그 분야에서 중요도가 가장 높은 이슈.
    - 중요도 표기 유지: 핵심(9점 이상)은 🔴, 그 외는 ·.
    - 200자를 넘으면 헤드라인을 단계적으로 줄이고, 그래도 넘치면
      중요도가 가장 낮은 분야를 뒤에서 뺀다 (발송 실패보다 낫다).
    """
    # 이슈번호 → 압축 헤드라인
    lines = {}
    for item in kakao_lines:
        if isinstance(item, dict):
            ci, line = item.get("cluster", -1), str(item.get("line", "")).strip()
        else:                       # 모델이 문자열 배열로 답한 경우의 안전장치
            ci, line = -1, str(item).strip()
        if line and 0 <= ci < len(clusters):
            lines[ci] = line

    # 분야별로 중요도가 가장 높은 이슈 하나만 남긴다
    best = {}
    for ci, line in lines.items():
        c = clusters[ci]
        cat, imp = c.get("category", "기타"), c.get("importance", 5)
        if cat not in best or imp > best[cat][0]:
            best[cat] = (imp, line)

    order = [c for c in CATEGORIES if c in best] + [c for c in best if c not in CATEGORIES]
    rows = [[cat, best[cat][0], best[cat][1]] for cat in order]

    header = f"{RUN_EMOJI} {TODAY_KR_SHORT} 브리핑 · {n}건\n\n"

    def clip(s, cut):
        return s if not cut or len(s) <= cut else s[:cut - 1] + "…"

    def render(cut):
        out = header
        for cat, imp, line in rows:
            label = KAKAO_CAT_LABELS.get(cat, cat)
            mark = "🔴 " if imp >= 9 else "· "
            out += f"{label} {mark}{clip(line, cut)}\n"
        return out.rstrip()

    text = render(0)
    for cut in (18, 16, 14, 12):            # 1) 헤드라인을 줄여 맞춘다
        if len(text) <= KAKAO_CHAR_LIMIT:
            break
        text = render(cut)
    while len(text) > KAKAO_CHAR_LIMIT and len(rows) > 1:   # 2) 약한 분야를 뺀다
        rows.pop(min(range(len(rows)), key=lambda i: rows[i][1]))
        text = render(12)

    return [text]


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    client = Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
    base_url = os.environ.get("PAGES_BASE_URL", "").rstrip("/")

    articles = collect_articles()
    if len(articles) < 5:
        raise SystemExit("수집된 기사가 너무 적습니다. 피드 설정을 확인하세요.")

    sent_log = load_sent_log()
    clusters = select_clusters(client, articles, sent_log)
    clusters = attach_texts(clusters, articles)
    summary_data = summarize(client, clusters)
    n = render_page(clusters, summary_data)
    save_sent_log(sent_log, [c["headline"] for c in clusters])

    page_url = f"{base_url}/{TODAY}-{RUN_SLUG}.html" if base_url else "https://github.com"
    try:
        token = kakao_get_access_token()
        msgs = build_kakao_messages(summary_data.get("kakao_lines", []), clusters, n)
        for i, m in enumerate(msgs):
            kakao_send(token, m, page_url,
                       button="브리핑 전체 보기" if i == 0 else "브리핑 열기")
        print(f"[발송] 카카오톡 {len(msgs)}건 전송 완료")
    except Exception:
        print("[경고] 카카오톡 발송 실패 — 브리핑 페이지는 정상 발행되었습니다.")
        traceback.print_exc()


if __name__ == "__main__":
    main()
