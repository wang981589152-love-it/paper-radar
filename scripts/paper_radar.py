#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
论文雷达 Paper Radar
抓取目标期刊（Europe PMC）近三年论文 -> 领域相关性打分 -> 生成本地网页数据

用法:
  python3 paper_radar.py probe             # 查看各期刊近三年论文数量
  python3 paper_radar.py full              # 首次全量抓取近三年（已有缓存则直接用缓存）
  python3 paper_radar.py full --refetch    # 忽略缓存重新全量抓取
  python3 paper_radar.py full --only PC    # 只抓某一刊（测试用）
  python3 paper_radar.py daily             # 增量更新最近10天
  python3 paper_radar.py figs              # 为 tier>=2 且有 PMC 全文的文章抓图形摘要

期刊抓取模式:
  mode="full"     植物专业期刊: 收录全部论文
  mode="filtered" 综合大刊(CNS等): 查询阶段用植物词预过滤, 入库只保留 tier>=1
"""
import json, re, time, hashlib, argparse, urllib.request, urllib.parse
import sys, os
from pathlib import Path
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "raw_cache.json"
FIGS_RAW = DATA / "figs_raw.json"
API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ------------------------------------------------------------------ 期刊配置
# mode: full=全收录(植物专业期刊) / filtered=预过滤+只保留tier>=1(CNS综合大刊)
JOURNALS = [
    # ---- 植物专业期刊（全收录）----
    dict(name="PNAS",                                 short="PNAS",       issns=["0027-8424", "1091-6490"], mode="full"),
    dict(name="The Plant Cell",                       short="PC",         issns=["1040-4651", "1532-298X"], mode="full"),
    dict(name="Nature Plants",                        short="NP",         issns=["2055-026X", "2055-0278"], mode="full"),
    dict(name="Molecular Plant",                      short="MP",         issns=["1674-2052", "1752-9867"], mode="full"),
    dict(name="New Phytologist",                      short="New Phytol", issns=["0028-646X", "1469-8137"], mode="full"),
    dict(name="Journal of Integrative Plant Biology", short="JIPB",       issns=["1672-9072", "1744-7909"], mode="full"),
    dict(name="The Plant Journal",                    short="Plant J",    issns=["0960-7412", "1365-313X"], mode="full"),
    dict(name="Journal of Experimental Botany",       short="JXB",        issns=["0022-0957", "1460-2431"], mode="full"),
    dict(name="Plant Physiology",                     short="Plant Physiol", issns=["0032-0889", "1532-2548"], mode="full"),
    dict(name="Plant Communications",                 short="Plant Commun", issns=["2590-3462"], mode="full"),
    dict(name="Cell Research",                        short="Cell Res",   issns=["1001-0602", "1748-7838"], mode="full"),
    dict(name="Annual Review of Plant Biology",       short="Annu Rev Plant Biol", issns=["1543-5008"], mode="full"),
    dict(name="Trends in Plant Science",              short="Trends Plant Sci", issns=["1360-1385", "1878-4372"], mode="full"),
    # ---- CNS 及子刊 / 综合大刊（预过滤，只收植物相关）----
    dict(name="Nature",                     short="Nature",     issns=["0028-0836", "1476-4687"], mode="filtered"),
    dict(name="Science",                    short="Science",    issns=["0036-8075", "1095-9203"], mode="filtered"),
    dict(name="Cell",                       short="Cell",       issns=["0092-8674", "1097-4172"], mode="filtered"),
    dict(name="Nature Communications",      short="Nat Commun", issns=["2041-1723"], mode="filtered"),
    dict(name="Science Advances",           short="Sci Adv",    issns=["2375-2548"], mode="filtered"),
    dict(name="Cell Reports",               short="Cell Rep",   issns=["2211-1247"], mode="filtered"),
    dict(name="eLife",                      short="eLife",      issns=["2050-084X"], mode="filtered"),
    dict(name="Current Biology",            short="Curr Biol",  issns=["0960-9822", "1879-0445"], mode="filtered"),
    dict(name="Developmental Cell",         short="Dev Cell",   issns=["1534-5807", "1878-1551"], mode="filtered"),
    dict(name="Cell Host & Microbe",        short="Cell Host Microbe", issns=["1931-3128", "1934-6069"], mode="filtered"),
    dict(name="Nature Genetics",            short="Nat Genet",  issns=["1061-4036", "1546-1718"], mode="filtered"),
    dict(name="Nature Biotechnology",       short="Nat Biotechnol", issns=["1087-0156", "1546-1696"], mode="filtered"),
    dict(name="Nature Chemical Biology",    short="Nat Chem Biol", issns=["1552-4450", "1552-4469"], mode="filtered"),
    dict(name="Nature Ecology & Evolution", short="Nat Ecol Evol", issns=["2397-334X"], mode="filtered"),
    dict(name="Nature Microbiology",        short="Nat Microbiol", issns=["2058-5276"], mode="filtered"),
    dict(name="Nature Cell Biology",        short="Nat Cell Biol", issns=["1465-7392", "1476-4679"], mode="filtered"),
    dict(name="The EMBO Journal",           short="EMBO J",     issns=["0261-4189", "1460-2075"], mode="filtered"),
    dict(name="Science Signaling",          short="Sci Signal", issns=["1937-9145", "1945-0877"], mode="filtered"),
]
SHORT2J = {j["short"]: j for j in JOURNALS}
EXCLUDE_TYPE_HINTS = ["correction", "erratum", "addendum", "retraction", "editorial",
                      "letter", "news", "comment", "meeting", "abstract"]

# 综合大刊预过滤词：标题或摘要含任一词才进入抓取范围（防止漏检可自行扩充）
PRE_FILTER = ["plant", "plants", "plantae", "arabidopsis", "rice", "oryza sativa",
              "wheat", "maize", "corn", "zea mays", "soybean", "tomato", "potato",
              "barley", "poplar", "tobacco", "cotton", "sorghum", "medicago",
              "chlamydomonas", "physcomitrella", "brachypodium", "setaria",
              "crop", "crops", "seedling", "seedlings", "strigolactone",
              "jasmonate", "jasmonic", "abscisic acid", "auxin",
              "brassinosteroid", "gibberellin", "cytokinin"]

# ------------------------------------------------------------- 打分关键词配置
TERMS = [
    ("gate", "植物", r"plant|arabidopsis|rice|oryza|wheat|maize|zea mays|tomato|tobacco|soybean|potato|poplar|barley|cotton|chlamydomonas|physcomitrella|medicago|setaria|sorghum|brachypodium|grapevine|citrus|algae|fern|moss|camellia|cucumber|pepper|legume|cereal|striga|orobanche|amborella|selaginella"),

    ("env", "干旱", r"drought|water[- ]deficit|dehydration|water stress"),
    ("env", "盐胁迫", r"salt stress|salinity|\bnacl\b|salt tolerance|halotolerance"),
    ("env", "高温", r"heat stress|heat shock|high[- ]temperature|thermotolerance|thermal stress"),
    ("env", "低温", r"cold stress|cold tolerance|freezing|chilling|cold acclimation|low temperature"),
    ("env", "光信号", r"light (signalling|signaling|quality|intensity)|photoreceptor|phytochrome|cryptochrome|phototropin|uvr8|shade|photomorphogenesis|photoperiod|red light|blue light|far[- ]red"),
    ("env", "病原与免疫", r"pathogen|pathogenesis|\bpamp\b|flg22|effector[- ]triggered|immune|immunity|\bdefen[cs]e\b|blight|mildew|fusarium|magnaporthe|pseudomonas syringae|botrytis|nematode|aphid|virus resistance"),
    ("env", "洪涝缺氧", r"flooding|waterlog|hypoxia|anoxia|submergence|submerged|low oxygen"),
    ("env", "营养元素", r"nitrogen|\bnitrate\b|ammonium|phosph(o|ate|orus)|potassium|\biron\b|zinc|nutrient (uptake|deficiency|starvation)|micronutrient|boron|manganese|sulfate"),
    ("env", "氧化与重金属", r"heavy metal|cadmium|aluminum|reactive oxygen species|\bros\b|oxidative stress|\buv\b|uv-?b"),
    ("env", "机械与触觉", r"mechanical (stimul|stress|wound)|touch|wounding|herbivory"),

    ("hormone", "ABA·脱落酸", r"abscisic acid|\baba\b"),
    ("hormone", "生长素", r"auxin|indole-3-acetic|\biaa\b|indoleacetic"),
    ("hormone", "乙烯", r"ethylene|1-aminocyclopropane|\bacc\b"),
    ("hormone", "茉莉酸", r"jasmonate|jasmonic|\bmeja\b|\bja\b|\bopda\b|coronatine"),
    ("hormone", "水杨酸", r"salicylic acid|salicylate|\bsa\b"),
    ("hormone", "赤霉素", r"gibberellin|\bga\b|della|gid1|ga20ox|ga3ox|ga2ox"),
    ("hormone", "油菜素甾醇", r"brassinosteroid|brassinolide|\bbr\b|bri1|bzr1|bes1"),
    ("hormone", "细胞分裂素", r"cytokinin|isopentenyltransferase|zeatin"),
    ("hormone", "独脚金内酯", r"strigolactone|karrikin|\bkai2\b|\bd14\b|max2"),

    ("tf", "转录因子(总称)", r"transcription (factor|regulator)|transcriptional (factor|regulator)"),
    ("tf", "MYB", r"\bmyb"),
    ("tf", "NAC", r"\bnac\b|\bnac\d"),
    ("tf", "WRKY", r"wrky"),
    ("tf", "ERF·AP2·DREB·CBF", r"\berf\b|\berfs\b|apetala2|\bap2\b|\bdreb\b|\bcbf\b|dehydration-responsive element"),
    ("tf", "bZIP·ABF·AREB", r"\bbzip\b|\babf\b|\bareb\b|\babi5\b|\btga\b"),
    ("tf", "bHLH·PIF·ICE", r"\bbhlh\b|\bpif\d?\b|phytochrome interacting|\bice1\b|\bice2\b"),
    ("tf", "TCP", r"\btcp\b|\btcp\d"),
    ("tf", "ARF", r"\barf\b|auxin response factor"),
    ("tf", "HD-Zip·WOX·Homeobox", r"hd-zip|homeobox|homeodomain|\bwox\b|knox|wuschel"),
    ("tf", "MADS", r"mads"),
    ("tf", "锌指·C2H2", r"zinc finger|\bc2h2\b|\bstop1\b"),
    ("tf", "GRAS·SCARECROW", r"\bgras\b|scarecrow|short-root|\bscl\b"),
    ("tf", "SPL·SBP", r"\bspl\b|spl\d|squamosa"),
    ("tf", "其他家族", r"yabby|\bgata\b|\blbd\b|nf-y|\bhsf\b|heat shock transcription|\bnin\b|\bnlp\b"),

    ("reg", "直接调控证据", r"directly regulat|direct target|binds? to the promoter|promoter (binding|region)|\bchip\b|chip-seq|chip-qpcr|\bemsa\b|electrophoretic mobility|luciferase reporter|dual-luciferase|transactivation|transcriptional (regulation|cascade|activation|repression|repressor)|yeast one-hybrid|\by1h\b|dap-seq|cut&run|atac-seq|transient expression|protoplast transfection"),

    ("growth", "生长发育", r"growth|development|root\b|hypocotyl|\bleaf\b|leaves|stomata|stomatal|flowering|senescence|germination|xylem|vascular|meristem|branching|tillering|\byield\b|biomass|lateral root|root hair|embryo"),
]
COMPILED = [(l, lab, re.compile(p, re.I)) for (l, lab, p) in TERMS]

# ------------------------------------------------------------------ HTTP 工具
def http_json(url, retries=4, timeout=90):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"请求失败: {url} -> {last}")

def clean_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"\s+", " ", s).strip()

# 机构关键词（中英/西/葡/德/法 常见机构词 + 简称），用于判断一个片段是否是独立机构
_ORG_KW = ("University", "Universidad", "Universidade", "Universität", "Université",
           "Univ ", "Institute", "Institut", "Instituto", "College", "Laboratory",
           "Laboratoire", "Lab ", "Laborat", "Center", "Centre", "Centro", "Academy",
           "Academia", "School", "Department", "Departamento", "Departament", "Hospital",
           "CNRS", "INSERM", "CEA", "CSIRO", "CIRAD", "INRA", "INRAE", "Research",
           "GmbH", "Ltd", "Inc", "AG ", "Facult", "Cluster", "Network", "Station",
           "Foundation", "Museum", "BGI", "Conservatory", "Botanical", "Collection",
           "Max-Planck", "Max Planck", "Graduate", "Faculdade", "Escola", "École")

def _is_org(seg):
    """判断一个单位片段是否是独立机构（含机构关键词，或明显非地址）"""
    return any(k.lower() in seg.lower() for k in _ORG_KW)

def _is_addr_tail(seg):
    """判断片段是否为纯地址尾巴（邮编/城市+国家模式，且不含机构词）"""
    if _is_org(seg):
        return False
    # 含邮编（3-6位数字块）或 ", City, Country" 结构，或纯地址前缀
    if re.search(r"\b\d{3,6}\b", seg):
        return True
    if re.search(r",\s*[A-ZÀ-Ž][a-zà-ž]+\s+\d", seg):  # 城市+邮编
        return True
    if re.search(r",\s*[A-ZÀ-Ž][a-zà-ž]+,\s*[A-ZÀ-Ž][a-zà-ž]+\.?$", seg):  # City, Country
        return True
    return False

def split_affiliations(affs):
    """把 Europe PMC 塞进同一字符串的多个机构拆开；纯地址尾巴归并入前一个机构。

    规则：
      - 清理末尾邮箱（Europe PMC 有时把邮箱塞进单位字段）
      - 按 ';' 切分；空段/句尾 'and.' 等噪声丢弃
      - 若片段是「地址尾巴」（邮编/城市+国家，且无机构词）→ 并入前一个机构（用逗号续接）
      - 若片段含机构关键词 → 作为独立机构
      - 兜底：无法判断的短片段，若前一个是机构则并入，否则作为独立项
    """
    out = []
    for af in affs:
        # 清理单位末尾混入的邮箱（含其前的句点/空格）
        af = re.sub(r"[\s.]*[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\s*\.?$", "", af)
        af = re.sub(r";\s*and\.?\s*$", "", af)  # 去掉末尾 "; and." 噪声
        parts = [p.strip().rstrip(".").strip() for p in af.split(";")]
        parts = [p for p in parts if p]
        if len(parts) <= 1:
            out.append(af)
            continue
        cur = ""
        for seg in parts:
            if not seg:
                continue
            if not cur:
                cur = seg
                continue
            if _is_addr_tail(seg):
                # 地址尾巴：并入前一个机构
                cur += ", " + seg
            elif _is_org(seg):
                # 独立机构：收尾前一个，开始新的
                out.append(cur)
                cur = seg
            else:
                # 无法判断：默认并入（避免把机构名误拆碎）
                cur += ", " + seg
        if cur:
            out.append(cur)
    # 去重保序
    seen, dedup = set(), []
    for a in out:
        if a not in seen:
            seen.add(a)
            dedup.append(a)
    return dedup

# ------------------------------------------------------------------ 抓取
def build_query(j, since, until):
    issn_q = " OR ".join('ISSN:"%s"' % i for i in j["issns"])
    q = 'SRC:MED AND (%s) AND FIRST_PDATE:[%s TO %s]' % (issn_q, since, until)
    if j.get("mode") == "filtered":
        pq = " OR ".join('(TITLE:"%s" OR ABSTRACT:"%s")' % (t, t) for t in PRE_FILTER)
        q += " AND (%s)" % pq
    return q

def fetch_journal(j, since, until):
    q = build_query(j, since, until)
    cursor, out, page = "*", [], 0
    while True:
        params = {"query": q, "format": "json", "pageSize": "1000",
                  "resultType": "core", "cursorMark": cursor, "sort": "P_PDATE_D asc"}
        data = http_json(API + "?" + urllib.parse.urlencode(params))
        results = data.get("resultList", {}).get("result", [])
        out.extend(results)
        page += 1
        print(f"    [{j['short']}] page {page}, 累计 {len(out)}/{data.get('hitCount','?')}", flush=True)
        nxt = data.get("nextCursorMark")
        if not results or not nxt or nxt == cursor:
            break
        cursor = nxt
        time.sleep(0.6)
    return out

# ------------------------------------------------------------------ 解析与打分
def make_id(rec, title):
    if rec.get("pmid"):
        return "pmid:" + rec["pmid"]
    if rec.get("doi"):
        return "doi:" + rec["doi"]
    return "t:" + hashlib.md5(title.encode("utf-8")).hexdigest()[:12]

def parse_record(rec, j):
    atype = (rec.get("articleType") or "").lower()
    if any(h in atype for h in EXCLUDE_TYPE_HINTS):
        return None
    title = clean_html(rec.get("title", ""))
    if not title:
        return None
    abstract = clean_html(rec.get("abstractText", "") or "")
    authors = []
    for a in rec.get("authorList", {}).get("author", []) or []:
        if a.get("collectiveName"):
            authors.append(a["collectiveName"])
        else:
            authors.append(((a.get("firstName", "") + " " + a.get("lastName", "")).strip()))
    affs = []
    # 优先从每个作者的 authorAffiliationDetailsList 提取（更全）
    for a in rec.get("authorList", {}).get("author", []) or []:
        det = a.get("authorAffiliationDetailsList") or {}
        for x in det.get("authorAffiliation", []) or []:
            af = clean_html(x.get("affiliation", ""))
            if af and af not in affs:
                affs.append(af)
    # fallback：顶层 affiliation 字段（core 类型返回的字符串）
    if not affs:
        top = clean_html(rec.get("affiliation", "") or "")
        if top:
            affs = [top]
    # 拆分串味的单位（Europe PMC 有时把多机构塞进同一字符串）
    affs = split_affiliations(affs)
    ji = rec.get("journalInfo", {}) or {}
    d = rec.get("firstPublicationDate") or ji.get("printPublicationDate") or ""
    d = (d or "")[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}", d):
        d = (str(rec.get("pubYear") or "") + "-01-01")
    return {
        "id": make_id(rec, title),
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "affs": affs,
        "date": d,
        "doi": rec.get("doi") or "",
        "pmid": rec.get("pmid") or "",
        "pmcid": rec.get("pmcid") or "",
        "journal": j["short"],
        "atype": atype,
    }

def score_paper(title, abstract):
    text = (title + " . " + abstract).lower()
    hits = {"env": [], "hormone": [], "tf": [], "reg": [], "growth": []}
    gate = False
    for layer, label, rx in COMPILED:
        if rx.search(text):
            if layer == "gate":
                gate = True
            else:
                hits[layer].append(label)
    if not gate:
        return 0, 0, []
    e, h, t, r, g = (len(hits[k]) for k in ("env", "hormone", "tf", "reg", "growth"))
    score = 2 * e + 3 * h + 2 * t + 2 * r + 1 * g
    if e and h: score += 3
    if h and t: score += 3
    if e and h and t: score += 5
    if t and r: score += 2
    tier = 3 if score >= 12 else (2 if score >= 7 else (1 if score >= 3 else 0))
    tags = (hits["env"] + hits["hormone"] + hits["tf"] + hits["reg"] + hits["growth"])[:8]
    return score, tier, tags

# ------------------------------------------------------------------ 图形摘要
def load_figs():
    if FIGS_RAW.exists():
        try:
            return json.loads(FIGS_RAW.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _fetch_fig_one(p):
    try:
        req = urllib.request.Request(
            "https://pmc.ncbi.nlm.nih.gov/articles/%s/" % p["pmcid"], headers={"User-Agent": UA})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        m = re.search(r'https://cdn\.ncbi\.nlm\.nih\.gov/pmc/blobs/[^"]+\.(?:jpg|jpeg|png|webp|gif)', html)
        if m:
            return p["id"], m.group(0)
    except Exception:
        pass
    return None

def fetch_figs(papers, workers=5, refetch=False):
    done = {} if refetch else load_figs()
    targets = [p for p in papers
               if p.get("tier", 0) >= 2 and p.get("pmcid") and p["id"] not in done]
    print(f"[figs] 已有 {len(done)} 篇图形, 待抓取 {len(targets)} 篇 (tier>=2 且有 PMC 全文)", flush=True)
    if targets:
        with ThreadPoolExecutor(workers) as ex:
            for i, r in enumerate(ex.map(_fetch_fig_one, targets)):
                if r:
                    done[r[0]] = r[1]
                if (i + 1) % 200 == 0:
                    print(f"[figs] 进度 {i+1}/{len(targets)}, 命中 {len(done)}", flush=True)
    DATA.mkdir(exist_ok=True)
    FIGS_RAW.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    print(f"[figs] 完成: 共 {len(done)} 篇带图形摘要 -> {FIGS_RAW}", flush=True)
    return done

# ------------------------------------------------------------------ 构建网页数据
def build(papers, figs=None):
    # filtered 模式的综合大刊只保留 tier>=1
    kept = []
    for p in papers:
        j = SHORT2J.get(p["journal"])
        if j and j.get("mode") == "filtered" and p.get("tier", 0) < 1:
            continue
        kept.append(p)
    kept.sort(key=lambda p: (p["date"], p.get("score", 0)), reverse=True)

    units = {}
    def unit_id(u):
        if u not in units:
            units[u] = str(len(units) + 1)
        return units[u]

    meta_list, full_by_year = [], {}
    for p in kept:
        au3 = ", ".join(p["authors"][:3]) + (" et al." if len(p["authors"]) > 3 else "")
        meta_list.append({
            "i": p["id"], "ti": p["title"], "jo": p["journal"], "da": p["date"],
            "sc": p.get("score", 0), "tr": p.get("tier", 0), "tg": p.get("tags", []),
            "au": au3, "doi": p["doi"], "pm": p["pmid"],
        })
        y = p["date"][:4] or "unknown"
        full_by_year.setdefault(y, {})[p["id"]] = {
            "ab": p["abstract"],
            "af": ";".join(unit_id(u) for u in p["affs"]),
            "aus": "; ".join(p["authors"]),
        }

    tiers = {0: 0, 1: 0, 2: 0, 3: 0}
    journals, monthly = {}, {}
    for p in kept:
        tiers[p.get("tier", 0)] = tiers.get(p.get("tier", 0), 0) + 1
        journals[p["journal"]] = journals.get(p["journal"], 0) + 1
        ym = p["date"][:7]
        m = monthly.setdefault(ym, {"n": 0, "s": 0})
        m["n"] += 1
        if p.get("tier", 0) >= 2:
            m["s"] += 1
    latest = kept[0]["date"] if kept else ""
    if latest:
        d7 = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
        recent7 = sum(1 for p in kept if p["date"] >= d7)
    else:
        recent7 = 0
    stats = {
        "built": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": len(kept), "tiers": tiers, "journals": journals,
        "monthly": dict(sorted(monthly.items())), "latest": latest, "recent7": recent7,
        "nJournals": len([j for j in journals]),
        "nFigs": len(figs) if figs else 0,
    }

    DATA.mkdir(exist_ok=True)
    inv = {v: k for k, v in units.items()}
    meta_js = "window.RADAR_UNITS=" + json.dumps(inv, ensure_ascii=False, separators=(",", ":")) + ";\n"
    meta_js += "window.RADAR_FIGS=" + json.dumps(figs or {}, ensure_ascii=False, separators=(",", ":")) + ";\n"
    meta_js += "window.RADAR_META=" + json.dumps({"stats": stats, "papers": meta_list}, ensure_ascii=False, separators=(",", ":")) + ";\n"
    (DATA / "meta.js").write_text(meta_js, encoding="utf-8")
    for y, fy in full_by_year.items():
        js = 'window.RADAR_FULL=window.RADAR_FULL||{};\nwindow.RADAR_FULL["%s"]=' % y
        js += json.dumps(fy, ensure_ascii=False, separators=(",", ":")) + ";\n"
        (DATA / ("full_%s.js" % y)).write_text(js, encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
    print(f"[build] 完成: {len(kept)} 篇 | meta.js + {len(full_by_year)} 个年度分片 -> {DATA}", flush=True)

# ------------------------------------------------------------------ 主流程
def load_cache():
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return None

def save_cache(papers):
    DATA.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(papers, ensure_ascii=False), encoding="utf-8")

def do_fetch(journals, since, until):
    parsed = []
    for short in journals:
        j = SHORT2J[short]
        print(f"[fetch] {j['name']} ({since} ~ {until}) ...", flush=True)
        recs = fetch_journal(j, since, until)
        ok = 0
        for rec in recs:
            p = parse_record(rec, j)
            if p:
                parsed.append(p); ok += 1
        print(f"[fetch] {short}: 原始 {len(recs)} 条 -> 有效 {ok} 篇", flush=True)
        time.sleep(0.8)
    return parsed

def apply_scores(papers):
    for p in papers:
        s, t, tags = score_paper(p["title"], p["abstract"])
        p["score"], p["tier"], p["tags"] = s, t, tags
    return papers

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["probe", "full", "daily", "figs"])
    ap.add_argument("--since", default="2023-09-16", help="全量抓取起始日（默认近三年）")
    ap.add_argument("--only", default="", help="只抓指定期刊 short，逗号分隔")
    ap.add_argument("--refetch", action="store_true", help="忽略缓存重新抓取")
    ap.add_argument("--workers", type=int, default=5, help="figs 并发数")
    args = ap.parse_args()

    if args.mode == "probe":
        for j in JOURNALS:
            q = build_query(j, args.since, str(date.today()))
            d = http_json(API + "?" + urllib.parse.urlencode(
                {"query": q, "format": "json", "pageSize": "1"}))
            print(f"{j['name']}\t{d.get('hitCount')}")
        return

    if args.mode == "figs":
        papers = load_cache() or []
        if not papers:
            print("[figs] 无缓存，请先运行 full"); return
        apply_scores(papers)
        figs = fetch_figs(papers, workers=args.workers)
        build(papers, figs)
        return

    if args.mode == "full":
        cached = None if args.refetch else load_cache()
        if cached is not None:
            print(f"[full] 使用缓存 {len(cached)} 篇（--refetch 可强制重新抓取）", flush=True)
            papers = cached
        else:
            shorts = [s.strip() for s in args.only.split(",") if s.strip()] if args.only \
                else [j["short"] for j in JOURNALS]
            papers = do_fetch(shorts, args.since, str(date.today()))
            save_cache(papers)
    else:  # daily
        cached = load_cache()
        until = str(date.today() + timedelta(days=1))
        if cached:
            latest = max(p["date"] for p in cached)
            since = str(datetime.strptime(latest, "%Y-%m-%d").date() - timedelta(days=10))
        else:
            print("[daily] 无缓存，改为全量抓取近三年", flush=True)
            since = args.since
        shorts = [s.strip() for s in args.only.split(",") if s.strip()] if args.only \
            else [j["short"] for j in JOURNALS]
        fresh = do_fetch(shorts, since, until)
        merged = {p["id"]: p for p in (cached or [])}
        n_new = 0
        for p in fresh:
            if p["id"] not in merged:
                n_new += 1
            merged[p["id"]] = p
        papers = list(merged.values())
        cutoff = (date.today() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
        dropped = len(papers)
        papers = [p for p in papers if p["date"] >= cutoff]
        save_cache(papers)
        print(f"[daily] 新增 {n_new} 篇，滚动清理 {dropped - len(papers)} 篇过期记录", flush=True)

    apply_scores(papers)
    build(papers, load_figs())

if __name__ == "__main__":
    main()
