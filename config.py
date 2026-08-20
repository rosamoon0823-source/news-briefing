# -*- coding: utf-8 -*-
"""
뉴스 브리핑 봇 설정 파일
- 이 파일만 수정하면 카테고리, 뉴스 소스, 뉴스 개수를 바꿀 수 있습니다.
- RSS 주소가 죽어도 봇은 멈추지 않고 해당 소스만 건너뜁니다 (실행 로그에 표시됨).
"""

SITE_TITLE = "모닝 브리핑"

# 브리핑에 담을 뉴스 개수 (읽는 피로도 조절의 핵심)
TOTAL_MAX = 15          # 하루 전체 최대 기사(이슈) 수
MAX_PER_CATEGORY = 3    # 카테고리당 최대 이슈 수
MIN_PER_CATEGORY = 1    # 카테고리당 최소 이슈 수 (매일 모든 분야를 훑기 위한 필수 커버리지)
                        # 주의: MIN_PER_CATEGORY x 카테고리 수 <= TOTAL_MAX 를 유지할 것

# 카카오톡은 텍스트 템플릿 200자 상한이라 1통에 담는다.
# 분야별 대표 이슈 1건씩만 넣고, 나머지는 브리핑 페이지에서 본다.
KAKAO_CHAR_LIMIT = 200

# 카톡 1통에 8개 분야를 담아야 하므로 분야명을 짧게 줄여 쓴다.
# 키는 CATEGORIES의 이름과 정확히 일치해야 한다.
KAKAO_CAT_LABELS = {
    "정치": "정치",
    "사회": "사회",
    "경제": "경제",
    "IT·기술": "IT·기술",
    "문화": "문화",
    "AI 동향": "AI",
    "광고업계": "광고",
    "서비스 기획": "기획",
}

# 수집 대상 시간 범위 (시간 단위) - 최근 26시간 내 기사만 수집
COLLECT_HOURS = 26

# 요약에 사용할 Claude 모델
CLAUDE_MODEL = "claude-sonnet-4-6"

# 카테고리별 RSS 소스
# - Google News RSS는 수백 개 언론사를 모아주기 때문에
#   "여러 매체가 보도한 굵직한 뉴스"를 골라내는 데 특히 유용합니다.
# - 새 소스를 추가하려면 주소만 리스트에 넣으면 됩니다.
GN = "hl=ko&gl=KR&ceid=KR:ko"  # Google News 한국어 공통 파라미터

CATEGORIES = {
    "정치": [
        "https://news.google.com/rss/headlines/section/topic/POLITICS?" + GN,
        "https://www.yna.co.kr/rss/politics.xml",
        "https://www.hani.co.kr/rss/politics/",
        "https://imnews.imbc.com/rss/news/news_01.xml",
    ],
    "사회": [
        "https://news.google.com/rss/headlines/section/topic/NATION?" + GN,
        "https://www.yna.co.kr/rss/society.xml",
        "https://imnews.imbc.com/rss/news/news_05.xml",
    ],
    "경제": [
        "https://news.google.com/rss/headlines/section/topic/BUSINESS?" + GN,
        "https://www.yna.co.kr/rss/economy.xml",
        "https://www.hankyung.com/feed/economy",
        "https://imnews.imbc.com/rss/news/news_04.xml",
    ],
    "IT·기술": [
        "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?" + GN,
        "https://rss.etnews.com/Section901.xml",
        "https://feeds.feedburner.com/zdkorea",
        "https://platum.kr/feed",
    ],
    "문화": [
        "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?" + GN,
        "https://www.yna.co.kr/rss/culture.xml",
        "https://imnews.imbc.com/rss/news/news_06.xml",
    ],
    "AI 동향": [
        "https://www.aitimes.com/rss/allArticle.xml",
        "https://news.google.com/rss/search?q=%EC%9D%B8%EA%B3%B5%EC%A7%80%EB%8A%A5%20OR%20%22%EC%83%9D%EC%84%B1%ED%98%95%20AI%22&" + GN,
        "https://techcrunch.com/category/artificial-intelligence/feed/",
    ],
    "광고업계": [
        "https://www.madtimes.org/rss/allArticle.xml",
        "https://news.google.com/rss/search?q=%EA%B4%91%EA%B3%A0%EB%8C%80%ED%96%89%EC%82%AC%20OR%20%EA%B4%91%EA%B3%A0%EC%97%85%EA%B3%84%20OR%20%EB%94%94%EC%A7%80%ED%84%B8%EB%A7%88%EC%BC%80%ED%8C%85&" + GN,
    ],
    "서비스 기획": [
        "https://news.google.com/rss/search?q=%22%EC%84%9C%EB%B9%84%EC%8A%A4%20%EA%B8%B0%ED%9A%8D%22%20OR%20%22%ED%94%84%EB%A1%9C%EB%8D%95%ED%8A%B8%20%EB%A7%A4%EB%8B%88%EC%A0%80%22%20OR%20%22IT%20%EA%B8%B0%ED%9A%8D%22&" + GN,
        "https://news.google.com/rss/search?q=%22UX%22%20OR%20%22%ED%94%84%EB%A1%9C%EB%8D%95%ED%8A%B8%22%20%EC%B1%84%EC%9A%A9%20OR%20%EC%A7%81%EA%B5%B0&" + GN,
        "https://platum.kr/feed",
    ],
}

# 네이버 뉴스 검색 API용 카테고리별 검색어
# (GitHub Secrets에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET가 등록된 경우에만 작동.
#  키가 없으면 자동으로 건너뛰므로 네이버 없이도 봇은 정상 동작합니다.)
NAVER_QUERIES = {
    "정치": "정치",
    "사회": "사회",
    "경제": "경제",
    "IT·기술": "IT 기술",
    "문화": "문화",
    "AI 동향": "인공지능",
    "광고업계": "광고업계",
    "서비스 기획": "서비스 기획",
}

# 카테고리별 성격 안내 (뉴스 선별 AI에게 전달되는 힌트)
CATEGORY_HINTS = {
    "정치": "국정 운영, 입법, 외교안보 등 파급력 큰 이슈 위주. 정쟁성 단신 제외.",
    "사회": "다수 국민에게 영향을 주는 사건·제도·판결 위주. 개별 사건사고 단신 제외.",
    "경제": "거시경제, 금리, 부동산, 주요 산업·기업의 큰 움직임 위주.",
    "IT·기술": "빅테크, 플랫폼, 통신, 신기술 등 업계 전반에 영향을 주는 뉴스 위주.",
    "문화": "사회적으로 화제가 된 콘텐츠·트렌드 위주. 연예인 가십 제외.",
    "AI 동향": "새 모델 출시, 주요 기업 전략, 규제, 업무 활용 트렌드. 실무자에게 유용한 것 우선.",
    "광고업계": "광고회사 실무자 관점. 대행사 동향, 매체·플랫폼 정책 변화, 마케팅 트렌드, 주목할 캠페인.",
    "서비스 기획": "서비스 기획자/PM 직군 관점. 프로덕트 트렌드, 주요 서비스 개편, 직군·채용 동향, 일하는 방식 변화.",
}
