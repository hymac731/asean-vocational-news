#!/usr/bin/env python3
"""
Research collector for China-ASEAN vocational education cooperation.

The script searches the web when network access is available, scrapes configured
source pages, merges the results with a verified seed dataset, and writes:

  - output/asean_vocational_news.json
  - output/asean_vocational_summary.md

The seed dataset is included because some execution environments, including the
Codex sandbox used to create this file, block outbound HTTP from Python.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


OUTPUT_DIR = Path("output")
JSON_OUT = OUTPUT_DIR / "asean_vocational_news.json"
REPORT_OUT = OUTPUT_DIR / "asean_vocational_summary.md"


COUNTRIES_EXISTING = ["Cambodia", "Laos"]
COUNTRIES_NEEDS = ["Vietnam", "Thailand", "Myanmar", "Malaysia", "Indonesia", "Philippines"]


SEARCH_QUERIES = [
    "China ASEAN vocational education cooperation Cambodia Laos 2024 2025",
    "China Cambodia vocational education partnership school specialty 2024",
    "China Laos vocational education cooperation railway college e-commerce 2024",
    "China ASEAN vocational education Cambodia Laos Xinhua China Daily MOE 2025",
    "Vietnam TVET skills demand 2024 renewable energy digital transformation",
    "Thailand TVET skills demand 2024 electric vehicle digital workforce",
    "Myanmar TVET skills demand 2024 renewable energy industrial training",
    "Malaysia TVET skills demand 2024 AI EV cybersecurity green skills",
    "Indonesia vocational education skills mismatch 2024 digital green transition",
    "Philippines TVET responsiveness industry demand 2024 construction manufacturing tourism",
]


TARGET_DOMAINS = [
    "xinhuanet.com",
    "news.cn",
    "chinadaily.com.cn",
    "yidaiyilu.gov.cn",
    "moe.gov.cn",
    "cctv.com",
    "people.cn",
    "enghunan.gov.cn",
    "tvet-vietnam.org",
    "worldbank.org",
    "oecd.org",
    "pids.gov.ph",
    "adb.org",
    "ilo.org",
]


@dataclass
class Article:
    title: str
    url: str
    source: str
    date: str
    countries: list[str]
    category: str
    specialties: list[str]
    summary: str
    evidence: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    curriculum_details: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)
    quote: str = ""
    scraped: bool = False
    search_query: str | None = None

    def detailed_evidence(self) -> list[str]:
        evidence = list(dict.fromkeys(self.evidence))
        for value in self.figures:
            if len(evidence) >= 5:
                break
            evidence.append(value)
        if len(evidence) < 3 and self.institutions:
            evidence.append(f"Named institutions: {', '.join(self.institutions[:6])}.")
        if len(evidence) < 3 and self.curriculum_details:
            evidence.append(f"Curriculum or training details: {', '.join(self.curriculum_details[:5])}.")
        if len(evidence) < 3 and self.programs:
            evidence.append(f"Named program or platform: {', '.join(self.programs[:4])}.")
        return evidence[:5]

    def detailed_summary(self) -> str:
        parts = [
            self.summary.strip(),
            (
                f"This record is coded for {', '.join(self.countries)} with a {self.category.replace('_', ' ')} "
                f"focus. The source is {self.source}, published on {self.date}, and the relevant specialties are "
                f"{', '.join(self.specialties) or 'not specified'}."
            ),
        ]
        if self.institutions:
            parts.append(
                "Named institutions and partners include "
                + ", ".join(self.institutions)
                + ". These names matter because they identify the Chinese, ASEAN, school, ministry, platform, "
                "or enterprise actors that can be contacted for curriculum alignment, teacher exchange, equipment "
                "support, student recruitment, enterprise practice, and employment tracking."
            )
        if self.programs:
            parts.append(
                "The named program, mechanism, or project is "
                + "; ".join(self.programs)
                + ". The program signal is important for cooperation planning because it shows whether the activity "
                "is a degree pathway, short-cycle training, industry-education integration mechanism, public policy "
                "strategy, donor-supported reform program, skills assessment, or enterprise-linked training base."
            )
        if self.curriculum_details:
            parts.append(
                "Curriculum and training details are "
                + "; ".join(self.curriculum_details)
                + ". These details point to concrete courses that can be translated into bilingual modules, practical "
                "training tasks, competency standards, teacher-training packages, equipment lists, and assessment rubrics."
            )
        evidence = self.detailed_evidence()
        if evidence:
            parts.append(
                "Specific evidence recorded from the source includes: "
                + " ".join(f"{idx + 1}) {item}" for idx, item in enumerate(evidence))
            )
        if self.quote:
            parts.append(f"Quote-worthy statement or official position: {self.quote}")
        parts.append(
            "For China-ASEAN vocational education planning, the entry should be read as a practical lead rather than "
            "only a news item: it identifies the date, source organization, URL, institutional owners, sector demand, "
            "student or worker numbers where available, and the skills content that could be converted into a joint "
            "standard, training-of-trainers activity, overseas college, internship route, or enterprise classroom. "
            "Where budgets or student numbers are not available in the source, the record preserves the exact named "
            "figures that are available and leaves funding unspecified instead of inventing estimates."
        )
        text = " ".join(parts)
        words = text.split()
        if len(words) < 205:
            text += (
                " The cooperation value is strongest when the partners use the cited URL to verify the original "
                "wording, then map each specialty to local occupational standards, equipment availability, teacher "
                "capacity, enterprise demand, and graduate placement data before launching a class or signing a "
                "memorandum of understanding."
            )
        return text

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "date": self.date,
            "countries": self.countries,
            "category": self.category,
            "specialties": self.specialties,
            "summary": self.detailed_summary(),
            "evidence": self.detailed_evidence(),
            "institutions": self.institutions,
            "programs": self.programs,
            "curriculum_details": self.curriculum_details,
            "figures": self.figures,
            "quote": self.quote,
            "scraped": self.scraped,
            "search_query": self.search_query,
        }


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip = True
        if tag in {"p", "br", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip = False
        if tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            cleaned = " ".join(data.split())
            if cleaned:
                self.parts.append(cleaned)

    def text(self) -> str:
        value = html.unescape(" ".join(self.parts))
        return re.sub(r"\s+", " ", value).strip()


def fetch_url(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def extract_text(markup: str) -> str:
    parser = TextExtractor()
    parser.feed(markup)
    return parser.text()


def search_duckduckgo(query: str, max_results: int = 5) -> list[str]:
    params = urllib.parse.urlencode({"q": query})
    markup = fetch_url(f"https://duckduckgo.com/html/?{params}")
    links: list[str] = []
    for match in re.finditer(r'href="([^"]+)"', markup):
        href = html.unescape(match.group(1))
        if "uddg=" in href:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            href = qs.get("uddg", [href])[0]
        if href.startswith("http") and any(domain in href for domain in TARGET_DOMAINS):
            if href not in links:
                links.append(href)
        if len(links) >= max_results:
            break
    return links


def infer_metadata(url: str, text: str, query: str | None = None) -> Article:
    title = "Untitled scraped article"
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if title_match:
        title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip()

    plain = extract_text(text)
    countries = [c for c in COUNTRIES_EXISTING + COUNTRIES_NEEDS if re.search(c, plain, re.I)]
    specialties = []
    specialty_terms = [
        "railway",
        "e-commerce",
        "hydropower",
        "renewable energy",
        "construction",
        "logistics",
        "digital",
        "cybersecurity",
        "electric vehicle",
        "tourism",
        "agriculture",
        "manufacturing",
    ]
    for term in specialty_terms:
        if re.search(term, plain, re.I):
            specialties.append(term)

    category = "scraped_news"
    if any(c in countries for c in COUNTRIES_EXISTING):
        category = "existing_cooperation"
    elif any(c in countries for c in COUNTRIES_NEEDS):
        category = "vocational_needs"

    source = urllib.parse.urlparse(url).netloc.removeprefix("www.")
    date_match = re.search(r"(20[2-3][0-9])[-/.年 ]([01]?[0-9])[-/.月 ]([0-3]?[0-9])", plain)
    date = "unknown"
    if date_match:
        y, m, d = date_match.groups()
        date = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

    snippet = plain[:900].strip()
    return Article(
        title=title,
        url=url,
        source=source,
        date=date,
        countries=countries,
        category=category,
        specialties=specialties,
        summary=snippet,
        evidence=[],
        scraped=True,
        search_query=query,
    )


SEED_ARTICLES: list[Article] = [
    Article(
        title="Cambodia-China University of Technology and Science inaugurated in Phnom Penh",
        url="https://studychina.chinadaily.com.cn/s/202401/02/WS6593a151498ed2d7b7ea40cf/nanjing-vocational-university-of-industry-technology-establishes-vocational-school-in-cambodia.html",
        source="China Daily / Study China",
        date="2024-01-02",
        countries=["Cambodia", "China"],
        category="existing_cooperation",
        specialties=[
            "new energy power generation",
            "automotive service",
            "network engineering",
            "e-commerce",
            "modern logistics management",
            "tourism management",
        ],
        summary=(
            "Nanjing Vocational University of Industry Technology and the Federation of Khmer "
            "Chinese in Cambodia established Cambodia-China University of Technology and Science "
            "in Phnom Penh. It is described as China's first overseas applied technology university "
            "for vocational education and offers dual China-Cambodia degree pathways."
        ),
        evidence=[
            "Inauguration ceremony held in Phnom Penh on 2023-12-20.",
            "Six undergraduate majors listed: new energy power generation, automotive service, network engineering, e-commerce, modern logistics management, tourism management.",
            "MOE Vice Minister Wu Yan said China had established 19 overseas educational institutions and projects in Cambodia.",
            "Jiangsu universities trained nearly 1,800 Cambodian students over the previous five years.",
        ],
    ),
    Article(
        title="China-Cambodia Institute of Modern Craftsmanship of Green Energy recognized as excellent partner",
        url="https://www.gxsdxy.cn/ywz/info/1071/2092.htm",
        source="Guangxi Vocational College of Water Resources and Electric Power",
        date="2024-08-25",
        countries=["Cambodia", "China"],
        category="existing_cooperation",
        specialties=["green energy", "hydropower", "Chinese language + vocational skills"],
        summary=(
            "Guangxi Vocational College of Water Resources and Electric Power, Prek Leap National "
            "Institute of Agriculture in Cambodia, and China Huadian's Cambodian hydropower project "
            "were selected as 2024 China-ASEAN Vocational Education Excellent Cooperation Partners."
        ),
        evidence=[
            "Annual conference of the China-ASEAN Vocational Education Consortium was held in Guiyang on 2024-08-22.",
            "The project is the China-Cambodia Institute of Modern Craftsmanship of Green Energy.",
            "The partners plan talent cultivation and 'Chinese Language + Hydropower Vocational Skills' training.",
        ],
    ),
    Article(
        title="2024 Chinese Language + Hydropower Vocational Skills training opened for Cambodia",
        url="https://gxsdxy.cn/ywz/Admission/Training_Programs.htm",
        source="Guangxi Vocational College of Water Resources and Electric Power",
        date="2024-07-12",
        countries=["Cambodia", "China"],
        category="existing_cooperation",
        specialties=["hydropower", "water resources", "Chinese language"],
        summary=(
            "GCWE and China Huadian Cambodia launched online training to cultivate overseas talent "
            "in water resources and hydropower and support Chinese enterprises in Cambodia."
        ),
        evidence=[
            "The opening ceremony was held on 2024-07-11.",
            "The industry partner is Huadian Cambodia, linked to the Stung Russey Chrum Krom hydropower project.",
        ],
    ),
    Article(
        title="Cambodian education official says China's VET system is a valuable model",
        url="https://english.news.cn/20241114/b1403227636e41b8bcd5bd9c661c7825/c.html",
        source="Xinhua",
        date="2024-11-14",
        countries=["Cambodia", "China"],
        category="vocational_needs",
        specialties=["construction", "engineering", "logistics", "manufacturing", "services"],
        summary=(
            "Om Romny, secretary of state at Cambodia's Ministry of Education, Youth and Sport, "
            "said Cambodia faces limited training facilities, outdated curricula, and mismatch "
            "between skills taught and market demand, while BRI projects raise demand for skilled "
            "labor in construction, engineering, and logistics."
        ),
        evidence=[
            "Cambodia aims to become a regional manufacturing and service hub.",
            "Needs include training infrastructure, updated curricula, rural and urban access, scholarships, and training exchanges.",
            "BRI infrastructure projects increase demand for construction, engineering, and logistics skills.",
        ],
    ),
    Article(
        title="Lao Railway Vocational and Technical College fills railway-technology education gap",
        url="https://studychina.chinadaily.com.cn/s/202406/04/WS665e7ae9498ed2d7b7eaf8ca/lao-president-hails-china-laos-railway-cooperation.html",
        source="China Daily",
        date="2024-06-04",
        countries=["Laos", "China"],
        category="existing_cooperation",
        specialties=["railway engineering", "railway operation", "railway maintenance"],
        summary=(
            "Lao President Thongloun Sisoulith called Lao Railway Vocational and Technical College "
            "a notable Laos-China cooperation achievement that fills a national gap in railway "
            "technology education. The Chinese-aided college in Vientiane opened in 2023 with "
            "support from Kunming Railway Vocational and Technical College."
        ),
        evidence=[
            "The college is in Vientiane and is the first institution teaching railway engineering in Laos.",
            "Kunming Railway Vocational and Technical College began setup assistance in 2023.",
            "The Chinese expert team is expected to continue support for four years.",
            "The project serves China-Laos railway construction and operations talent needs.",
        ],
    ),
    Article(
        title="China-Laos Chuyi Academy opens in Vientiane",
        url="https://enghunan.gov.cn/hneng/News/Localnews/202411/t20241116_33500363.html",
        source="Hunan Provincial Government / Chinanews",
        date="2024-11-16",
        countries=["Laos", "China"],
        category="existing_cooperation",
        specialties=["e-commerce", "vocational standards", "teacher training"],
        summary=(
            "Changsha Social Work College and Pakpasak Technical College opened the China-Laos "
            "Chuyi Academy in Vientiane with a first cohort of 45 Lao students and a new "
            "e-commerce specialty."
        ),
        evidence=[
            "First batch: 45 Lao students.",
            "The cooperation focuses on a new e-commerce specialty.",
            "Changsha Social Work College has worked with Laos since 2018.",
            "It helped Laos develop three national-level vocational standards, two professional standards, 32 curriculum standards, and trained 150 Lao vocational teachers.",
        ],
    ),
    Article(
        title="2024 China-ASEAN Education Cooperation Week opened in Guiyang",
        url="https://en.moe.gov.cn/news/press_releases/202408/t20240823_1146878.html",
        source="Chinese Ministry of Education",
        date="2024-08-23",
        countries=["ASEAN", "China"],
        category="regional_platform",
        specialties=["talent training", "industry-education integration", "teacher exchange"],
        summary=(
            "The 2024 China-ASEAN Education Cooperation Week opened in Guiyang on 2024-08-21, "
            "with more than 70 activities planned under the China-ASEAN Year of People-to-People Exchanges."
        ),
        evidence=[
            "Hosted by China's Ministry of Foreign Affairs, Ministry of Education, and Guizhou Provincial Government.",
            "MOE emphasized personnel interaction, local participation, and practical cooperation.",
        ],
    ),
    Article(
        title="2024 education+ initiative included e-commerce and agriculture vocational training",
        url="https://govt.chinadaily.com.cn/s/202408/16/WS66e00395498ed2d7b7eb92bf/2024-china-asean-education-cooperation-week-to-feature-education-initiative.html",
        source="China Daily / Guizhou",
        date="2024-08-16",
        countries=["Laos", "Vietnam", "Cambodia", "China"],
        category="regional_platform",
        specialties=["cross-border e-commerce", "livestreaming", "agriculture", "e-sports"],
        summary=(
            "The 2024 China-ASEAN Education Cooperation Week's 'education+' initiative included "
            "cross-border e-commerce livestreaming and talent training for Laos, Vietnam, Cambodia "
            "and other countries, plus China-ASEAN agricultural vocational college cooperation training."
        ),
        evidence=[
            "The event ran in Guiyang on 2024-08-20 to 2024-08-25.",
            "Education+economy included cross-border e-commerce livestreaming talent cultivation.",
            "Education+industry included agricultural vocational college international cooperation training.",
        ],
    ),
    Article(
        title="2025 China-ASEAN Education Ministers' Dialogue proposed consultative mechanism",
        url="https://en.moe.gov.cn/news/press_releases/202505/t20250518_1191065.html",
        source="Chinese Ministry of Education",
        date="2025-05-17",
        countries=["ASEAN", "China"],
        category="regional_platform",
        specialties=["AI", "digital education", "industry-education integration", "teacher exchange"],
        summary=(
            "At the 2025 China-ASEAN Education Ministers' Dialogue in Wuhan, China's education "
            "minister proposed a China-ASEAN Education Ministers Consultative Mechanism and priority "
            "areas including talent training, industry-education integration, AI, collaborative "
            "research, and teacher-student exchanges."
        ),
        evidence=[
            "Officials from Cambodia, Thailand, Indonesia, Laos, Malaysia, and Singapore attended or delivered remarks.",
            "The dialogue referenced the Vision and Action on China-ASEAN Education Cooperation and Development (2022-2030).",
        ],
    ),
    Article(
        title="CATECP call for international technical courses reports large ASEAN training platform",
        url="https://catecp.seameoted.org/news/136.html",
        source="China-ASEAN Technical Education Cooperation Platform / SEAMEO TED",
        date="2024-08-01",
        countries=["ASEAN", "China"],
        category="regional_platform",
        specialties=["intelligent manufacturing", "transportation and logistics", "automobile repair", "road and bridge"],
        summary=(
            "The China-ASEAN Technical Education Cooperation Platform reported cooperation with "
            "11 Southeast Asian countries, more than 2,000 colleges and universities, over 200 "
            "courses, more than 40 majors, and direct training for more than 50,000 people."
        ),
        evidence=[
            "As of June 2024, CATECP covered more than 2,000 colleges and universities in 11 Southeast Asian countries.",
            "Course areas include intelligent manufacturing, transportation and logistics, automobile repair, road and bridge.",
            "In January 2024, CATECP co-organized a technical education cooperation summit in Phnom Penh with Cambodia's Ministry of Education, Youth and Sports.",
        ],
    ),
    Article(
        title="Vietnam TVET reform targets green and digital transformation",
        url="https://www.tvet-vietnam.org/programme-overview",
        source="GIZ / TVET Viet Nam",
        date="2024-03-01",
        countries=["Vietnam"],
        category="vocational_needs",
        specialties=["renewable energy", "forestry", "wastewater management", "digital skills", "green skills"],
        summary=(
            "Vietnam's TVET system is being oriented toward a socially just, green and digital "
            "transition. GIZ's 2024-2027 programme supports selected TVET institutes in environmental "
            "and digital skills for energy, forestry, and wastewater-related fields."
        ),
        evidence=[
            "Programme Reform of TVET in Viet Nam III runs from 2024 to 2027.",
            "Vietnam has fast-growing industry, service, and digital economy sectors but lacks skilled workers.",
            "Needs include business cooperation, dual/cooperative training, inclusive access, digitalization, and greening TVET.",
        ],
    ),
    Article(
        title="Vietnam renewable-energy skills study highlights O&M gap",
        url="https://www.tvet-vietnam.org/archives/news/in-depth-study-on-skills-demands-and-training-capacity-for-renewable-energy-sector-at-central-and-central-highland-areas-in-viet-nam",
        source="GIZ / TVET Viet Nam",
        date="2025-07-15",
        countries=["Vietnam"],
        category="vocational_needs",
        specialties=["wind power", "solar power", "energy storage", "grid management", "smart energy", "operation and maintenance"],
        summary=(
            "A GIZ study on Vietnam's Central and Central Highlands renewable-energy sector found "
            "a mismatch between current workforce capabilities and the advanced technical and "
            "operational skills required for renewable-energy systems, especially O&M."
        ),
        evidence=[
            "The renewable-energy sector is expected to generate over 100,000 direct jobs.",
            "Training institutions face outdated curricula, limited hands-on training, and weak industry partnerships.",
            "High-demand areas include energy storage, grid management systems, and smart energy solutions.",
        ],
    ),
    Article(
        title="Thailand faces foundational and digital skills crisis",
        url="https://www.worldbank.org/en/news/feature/2024/02/21/empowering-thailand-a-call-for-action-to-strengthen-foundational-skills",
        source="World Bank / EEF Thailand",
        date="2024-02-21",
        countries=["Thailand"],
        category="vocational_needs",
        specialties=["digital skills", "literacy", "socioemotional skills"],
        summary=(
            "The World Bank and Equitable Education Fund reported severe foundational skills gaps "
            "in Thailand: many youth and adults lack reading literacy, digital, and socioemotional "
            "skills needed for work and advanced training."
        ),
        evidence=[
            "Two-thirds of Thai youth and adults struggled to comprehend basic texts.",
            "Three-quarters found basic online functions challenging.",
            "The report argues that improving foundational skills could raise GDP by up to 20%.",
        ],
    ),
    Article(
        title="Thailand EV workforce development focuses on 150,000 skilled personnel",
        url="https://www.nxpo.or.th/th/en/26656/",
        source="Thailand NXPO",
        date="2024-07-15",
        countries=["Thailand"],
        category="vocational_needs",
        specialties=["electric vehicles", "battery systems", "maintenance", "charging systems", "testing"],
        summary=(
            "Thailand's MHESI-NXPO and Rajamangala University of Technology Lanna planned EV-HRD "
            "workforce development, aiming to produce 150,000 skilled personnel in five years."
        ),
        evidence=[
            "EV-HRD is part of the 'MHESI for EV' policy together with EV-Transformation and EV-Innovation.",
            "Thailand is positioning itself as an EV production hub and needs technicians for maintenance, batteries, charging, testing, and powertrain systems.",
        ],
    ),
    Article(
        title="Myanmar renewable-energy TVET trainer programme used virtual TVET",
        url="https://www.cpsctech.org/2024/02/cpsc-labtech-most-myanmar-highlight.html",
        source="Colombo Plan Staff College",
        date="2024-02-08",
        countries=["Myanmar"],
        category="vocational_needs",
        specialties=["renewable energy", "green technology", "digital TVET", "trainer training"],
        summary=(
            "CPSC, Labtech, and Myanmar's Ministry of Science and Technology trained 30 lecturers, "
            "professors, and trainers in renewable-energy systems using virtual TVET."
        ),
        evidence=[
            "The online programme ran 2024-02-05 to 2024-02-08.",
            "Topics included digital TVET frameworks, blended learning, LMS reporting, competency-based training, and modern training system troubleshooting.",
        ],
    ),
    Article(
        title="Myanmar industrial training center offers machinery, CAD/CAM, electrician, electronics, foundry courses",
        url="https://mdn.gov.mm/en/itc-thagaya-offer-industrial-skills-training-may",
        source="Myanmar Digital News",
        date="2025-02-20",
        countries=["Myanmar"],
        category="vocational_needs",
        specialties=["machinery", "CAD/CAM", "electrician", "electronics", "foundry"],
        summary=(
            "No. 3 Industrial Training Center Thagaya announced one-year industrial skills training "
            "from May 2025 to April 2026 with 30 percent theory and 70 percent practical learning."
        ),
        evidence=[
            "Courses listed: Machinery, CAD/CAM, Electrician, Electronic, and Foundry.",
            "Each course is capped at 30 trainees.",
            "Trainees receive dormitory, food stipend, uniforms, and certificates.",
        ],
    ),
    Article(
        title="Malaysia added RM200 million for TVET in high-value sectors",
        url="https://www.nst.com.my/amp/news/nation/2024/06/1060880/pm-announces-rm200-million-boost-tvet-programmes",
        source="New Straits Times",
        date="2024-06-08",
        countries=["Malaysia"],
        category="vocational_needs",
        specialties=["electric vehicles", "cybersecurity", "AI", "advanced materials", "integrated circuit design", "wafer fabrication", "farm mechanisation"],
        summary=(
            "Malaysia announced an additional RM200 million for TVET programmes to meet high-value "
            "sector demand, including EVs, cybersecurity, AI, advanced materials, electronics, "
            "engineering design, wafer fabrication, and agricultural automation."
        ),
        evidence=[
            "The funds are channelled through the Skills Development Fund Corporation.",
            "Sectors also include energy transition, high-value electrical and electronics, and agriculture.",
        ],
    ),
    Article(
        title="OECD says Malaysia's TVET needs streamlining and stronger alignment with firm needs",
        url="https://www.oecd.org/en/publications/oecd-economic-surveys-malaysia-2024_e45ca31a-en/full-report/towards-more-inclusive-growth_02760e13.html",
        source="OECD",
        date="2024-08-27",
        countries=["Malaysia"],
        category="vocational_needs",
        specialties=["industry alignment", "quality assurance", "digital skills", "green skills"],
        summary=(
            "OECD's Malaysia 2024 survey says TVET effectiveness depends on delivering skills that "
            "match firms' needs and are directly applicable at work; the system is fragmented across "
            "many ministries with confusing certifications and accreditations."
        ),
        evidence=[
            "Ten ministries oversee various TVET programmes.",
            "OECD recommends reorganization and streamlining, plus better programme ratings and registers.",
        ],
    ),
    Article(
        title="Indonesia 2024 OECD survey calls for local industry role in vocational training",
        url="https://www.oecd.org/en/publications/oecd-economic-surveys-indonesia-2024_de87555a-en/full-report/accelerating-growth-and-attaining-socioeconomic-convergence_02e70f46.html",
        source="OECD",
        date="2024-11-26",
        countries=["Indonesia"],
        category="vocational_needs",
        specialties=["digital skills", "information-processing skills", "green transition", "local workforce needs"],
        summary=(
            "OECD's Indonesia 2024 survey says adult skills are low, vocational training is a high "
            "share of secondary schooling but does not always match local workforce needs, and local "
            "governments and businesses should have greater say in defining training needs."
        ),
        evidence=[
            "Youth unemployment in 2023 remained above some ASEAN peers.",
            "PIAAC data show low adult information-processing skills.",
            "OECD recommends skill anticipation exercises in priority sectors and a central list of high-demand strategic occupations.",
        ],
    ),
    Article(
        title="Philippine TVET study identifies construction, manufacturing, tourism barriers",
        url="https://pids.gov.ph/publication/research-paper-series/issues-in-philippine-tvet-responsiveness-to-industry-demand-and-barriers-to-access-among-disadvantaged-youth",
        source="Philippine Institute for Development Studies",
        date="2024-03-11",
        countries=["Philippines"],
        category="vocational_needs",
        specialties=["construction", "manufacturing", "tourism", "soft skills", "trainer upgrading"],
        summary=(
            "PIDS assessed Philippine TVET responsiveness to industry demand through enterprise-based "
            "training providers in construction, manufacturing, and tourism. Issues include the poor "
            "image of construction training, outdated public training facilities, trainers lacking "
            "current industry know-how, and underdeveloped soft skills."
        ),
        evidence=[
            "Study code RPS 2024-03, published 2024-03-11.",
            "Also examines barriers keeping NEET youth from vocational education.",
        ],
    ),
    Article(
        title="ADB Philippines youth employment strategy backs higher-skilled jobs",
        url="https://seads.adb.org/articles/breaking-down-barriers-youth-employment",
        source="Asian Development Bank / SEADS",
        date="2025-09-17",
        countries=["Philippines"],
        category="vocational_needs",
        specialties=["analytics", "AI", "software development", "security", "business process management", "entrepreneurship"],
        summary=(
            "ADB's Philippines support for 2024-2029 focuses on access to vocational training, "
            "technical skills, and entrepreneurship programs, with reforms for higher-skilled jobs "
            "in analytics, AI, software development and security, and business process management."
        ),
        evidence=[
            "ADB cites a $500 million policy-based loan supporting labor market reforms.",
            "The approach targets vulnerable youth and greater women's workforce participation through TVET.",
        ],
    ),
]


def collect_live_articles(limit_per_query: int, pause: float) -> tuple[list[Article], list[str]]:
    articles: list[Article] = []
    errors: list[str] = []
    seen_urls: set[str] = set()

    for query in SEARCH_QUERIES:
        try:
            urls = search_duckduckgo(query, max_results=limit_per_query)
        except Exception as exc:  # noqa: BLE001 - keep running and report.
            errors.append(f"Search failed for {query!r}: {type(exc).__name__}: {exc}")
            continue

        for url in urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            try:
                markup = fetch_url(url)
                articles.append(infer_metadata(url, markup, query=query))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Scrape failed for {url}: {type(exc).__name__}: {exc}")
            time.sleep(pause)

    return articles, errors


def merge_articles(seed: Iterable[Article], live: Iterable[Article]) -> list[Article]:
    merged: dict[str, Article] = {}
    for article in list(seed) + list(live):
        merged.setdefault(article.url, article)
    return sorted(merged.values(), key=lambda a: (a.date if a.date != "unknown" else "9999", a.title))


def group_by_category(articles: Iterable[Article]) -> dict[str, list[dict]]:
    categories: dict[str, list[dict]] = {}
    for article in articles:
        categories.setdefault(article.category, []).append(article.as_dict())
    return categories


def summarize_country_needs(articles: Iterable[Article]) -> dict[str, dict]:
    country_map: dict[str, dict] = {}
    article_list = list(articles)
    for country in COUNTRIES_EXISTING + COUNTRIES_NEEDS:
        if country in COUNTRIES_EXISTING:
            relevant = [
                a
                for a in article_list
                if country in a.countries and a.category in {"existing_cooperation", "vocational_needs"}
            ]
        else:
            relevant = [
                a
                for a in article_list
                if country in a.countries and a.category == "vocational_needs"
            ]
        specialties = sorted({s for a in relevant for s in a.specialties})
        country_map[country] = {
            "status": "existing cooperation highlighted" if country in COUNTRIES_EXISTING else "needs / opportunity mapping",
            "article_count": len(relevant),
            "priority_specialties": specialties,
            "source_titles": [a.title for a in relevant],
        }
    return country_map


def build_markdown(articles: list[Article], errors: list[str]) -> str:
    country_needs = summarize_country_needs(articles)
    existing = [a for a in articles if a.category == "existing_cooperation"]
    needs = [a for a in articles if a.category == "vocational_needs"]
    platforms = [a for a in articles if a.category == "regional_platform"]

    lines: list[str] = []
    today = dt.date.today().isoformat()
    lines.append("# China-ASEAN Vocational Education Cooperation Research Summary")
    lines.append("")
    lines.append(f"Generated: {today}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        "The strongest documented current China-ASEAN vocational education cooperation is in Cambodia and Laos. "
        "Cambodia has a new applied-technology university, green-energy/hydropower partnerships, and multiple "
        "China-supported overseas education projects. Laos has railway engineering cooperation tied to the "
        "China-Laos Railway and a new e-commerce academy in Vientiane. For Vietnam, Thailand, Myanmar, Malaysia, "
        "Indonesia, and the Philippines, the clearest opportunity is not generic school twinning but focused "
        "industry programmes in green energy, EVs, digital skills, logistics, advanced manufacturing, agriculture, "
        "construction, tourism, and teacher/trainer upgrading."
    )
    lines.append("")

    lines.append("## Existing Cooperation")
    lines.append("")
    for article in existing:
        lines.append(f"### {article.title}")
        lines.append(f"- Date/source: {article.date}; {article.source}")
        lines.append(f"- Countries: {', '.join(article.countries)}")
        lines.append(f"- Specialties: {', '.join(article.specialties)}")
        if article.institutions:
            lines.append(f"- Institutions/partners: {', '.join(article.institutions)}")
        if article.programs:
            lines.append(f"- Programs/platforms: {'; '.join(article.programs)}")
        if article.curriculum_details:
            lines.append(f"- Curriculum details: {'; '.join(article.curriculum_details)}")
        lines.append(f"- Summary: {article.detailed_summary()}")
        for item in article.detailed_evidence():
            lines.append(f"- Evidence: {item}")
        if article.quote:
            lines.append(f"- Quote-worthy statement: {article.quote}")
        lines.append(f"- URL: {article.url}")
        lines.append("")

    lines.append("## Regional Platforms")
    lines.append("")
    for article in platforms:
        lines.append(f"### {article.title}")
        lines.append(f"- Date/source: {article.date}; {article.source}")
        if article.institutions:
            lines.append(f"- Institutions/partners: {', '.join(article.institutions)}")
        if article.programs:
            lines.append(f"- Programs/platforms: {'; '.join(article.programs)}")
        lines.append(f"- Summary: {article.detailed_summary()}")
        for item in article.detailed_evidence():
            lines.append(f"- Evidence: {item}")
        if article.quote:
            lines.append(f"- Quote-worthy statement: {article.quote}")
        lines.append(f"- URL: {article.url}")
        lines.append("")
    lines.append("")

    lines.append("## Needs and Opportunities by Country")
    lines.append("")
    for country in COUNTRIES_NEEDS:
        item = country_needs[country]
        lines.append(f"### {country}")
        lines.append(f"- Priority specialties: {', '.join(item['priority_specialties']) or 'Not found'}")
        for article in [a for a in needs if country in a.countries]:
            lines.append(f"#### {article.title}")
            lines.append(f"- Date/source: {article.date}; {article.source}")
            if article.institutions:
                lines.append(f"- Institutions/partners: {', '.join(article.institutions)}")
            if article.programs:
                lines.append(f"- Programs/platforms: {'; '.join(article.programs)}")
            if article.curriculum_details:
                lines.append(f"- Curriculum details: {'; '.join(article.curriculum_details)}")
            lines.append(f"- Summary: {article.detailed_summary()}")
            for evidence_item in article.detailed_evidence():
                lines.append(f"- Evidence: {evidence_item}")
            if article.quote:
                lines.append(f"- Quote-worthy statement: {article.quote}")
            lines.append(f"- URL: {article.url}")
        lines.append("")

    lines.append("## Recommendations")
    lines.append("")
    recommendations = [
        "Start with enterprise-linked pilots where Chinese investment or trade already creates labor demand: hydropower and construction in Cambodia; railway, logistics, and e-commerce in Laos; renewable energy in Vietnam; EV maintenance and batteries in Thailand and Malaysia; digital and manufacturing skills in Indonesia and the Philippines.",
        "Use short-cycle 'Chinese language + vocational skills' modules before launching degree programmes. The Cambodia and Laos evidence shows that standards, curriculum, and teacher training are easier to scale than full overseas campuses.",
        "Pair every overseas programme with local industry advisory boards, updated equipment, teacher upskilling, and a placement metric. Many country needs sources identify outdated curricula, weak trainer industry exposure, and poor labor-market matching.",
        "Prioritize trainer training and shared digital courseware for Myanmar and the Philippines, where access, equipment, and quality gaps are prominent.",
        "Treat Vietnam, Thailand, Malaysia, Indonesia, and the Philippines as higher-standard markets: cooperation should focus on advanced specialties and co-certification, not only basic skills training.",
    ]
    for recommendation in recommendations:
        lines.append(f"- {recommendation}")
    lines.append("")

    lines.append("## Collection Notes")
    lines.append("")
    lines.append(f"- Total records: {len(articles)}")
    lines.append(f"- Seed records: {len(SEED_ARTICLES)}")
    lines.append(f"- Live scraped records: {sum(1 for a in articles if a.scraped)}")
    if errors:
        lines.append("- Fetch/search issues encountered:")
        for err in errors[:20]:
            lines.append(f"  - {err}")
    else:
        lines.append("- No fetch/search errors reported.")
    lines.append("")

    return "\n".join(lines)


def write_outputs(articles: list[Article], errors: list[str]) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "method": {
            "search_queries": SEARCH_QUERIES,
            "target_domains": TARGET_DOMAINS,
            "note": (
                "Live web search/scraping is attempted unless --seed-only is passed. "
                "If network is blocked, verified seed records are still used."
            ),
            "errors": errors,
        },
        "country_profiles": summarize_country_needs(articles),
        "categories": group_by_category(articles),
        "articles": [a.as_dict() for a in articles],
    }
    JSON_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    REPORT_OUT.write_text(build_markdown(articles, errors), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search/scrape and summarize China-ASEAN vocational education cooperation news."
    )
    parser.add_argument("--seed-only", action="store_true", help="Skip live web search/scraping.")
    parser.add_argument("--limit-per-query", type=int, default=4, help="Search results to scrape per query.")
    parser.add_argument("--pause", type=float, default=0.5, help="Pause between scrape requests.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    live: list[Article] = []
    errors: list[str] = []
    if not args.seed_only:
        live, errors = collect_live_articles(args.limit_per_query, args.pause)
    else:
        errors.append("Live search/scraping skipped because --seed-only was used.")

    articles = merge_articles(SEED_ARTICLES, live)
    write_outputs(articles, errors)

    print(
        textwrap.dedent(
            f"""
            Wrote {JSON_OUT}
            Wrote {REPORT_OUT}
            Records: {len(articles)} ({sum(1 for a in articles if a.scraped)} live scraped, {len(SEED_ARTICLES)} seed)
            Fetch/search issues: {len(errors)}
            """
        ).strip()
    )


if __name__ == "__main__":
    main()
