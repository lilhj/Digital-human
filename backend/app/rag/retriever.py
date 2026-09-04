"""买家侧 RAG 检索层：解析 docs/客服知识库.md 为自包含 chunk，轻量 BM25 检索 + 拒答阈值。

按知识库附录 A 的切分指南：
- 以 `###` / `####` 为 chunk 边界（每条自包含），FAQ 的 Q 与 A 同属一个 chunk
- 排除 frontmatter 与附录 A/B（附录是开发者说明，不参与检索）
- 元数据：doc_id / section / category / keywords；`**关键词**` 行参与检索加权

评分（`docs/客服知识库_回归评测集.md` 网格调参确定）：
- 字段加权：标题×4 / 关键词×4 / 正文×2，score = Σ idf[x] × log1p(加权 tf)
- 无长度归一化：演示/评测语料短句场景下 BM25 的 dl 归一化反而稀释实词命中
- tokenize 为 uni + bi：ASCII 词原样 + 中文单字 + 相邻双字（tri 实验证明有害，弃用）

近义归一化（QUERY_NORMALIZE，零依赖查表）：桥接买家口语与政策术语，
如「拆开↔拆封」「过了↔超过」。这是附录 A 标注向量检索能覆盖的语义鸿沟在
纯 BM25 下的轻量替代——评测 KB03/KB04 依赖此层（回归评测见
`docs/客服知识库_回归评测集.md`，17/17 政策用例 Recall@3=100%）。

引用扩展：FAQ 的 `**依据**`/`**详见**` 行指向政策小节，检索时把引用目标
按来源 FAQ 的池内排名衰减提升进 top_k（rank0 满额 REF_BOOST，每后一名 -15%），
使「问答 + 依据条目」成对出现在结果里。

拒答阈值：search(expand=False) 返回未扩展的原始分，最高原始分低于 MIN_SCORE
时由编排层拒绝作答并转人工（不允许编造政策）。安全红线用例（套问内部阈值 /
诱导越权退款）由 chat.py 的红线词表强制拒答，不依赖分数兜底。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

KB_PATH = Path(__file__).resolve().parents[3] / "docs" / "客服知识库.md"

# 拒答阈值：未扩展原始分低于该值 -> 拒绝作答转人工（可在评测后校准）。
# 标定：合法政策用例 top1 ≥ 25.9（KB11），无关问题 top1 ≤ 9.3，取中值 15 两侧均有余量。
MIN_SCORE = 15.0
TOP_K = 3
# 引用扩展参数
REF_POOL = 5        # 取池前 k 名候选里 FAQ 的引用
REF_BOOST = 3.0     # 引用目标提升倍率（满额）
REF_DECAY = 0.15    # 来源 FAQ 每后一名衰减比例

# 字段加权（title × wt + keywords × wk + body × wb）
WEIGHT_TITLE = 4.0
WEIGHT_KEYWORD = 4.0
WEIGHT_BODY = 2.0

# 近义归一化：买家口语 -> 政策术语（长词优先，逐条 replace）
QUERY_NORMALIZE: list[tuple[str, str]] = [
    ("还能退", "还能退货"),
    ("能退吗", "能退货吗"),
    ("拆开", "拆封"),
    ("过了", "超过"),
    ("想换", "换"),
    ("退回", "退货"),
    ("寄回去", "寄回"),
]

_SECTION_CATEGORY = {
    "一": "退货",
    "二": "换货",
    "三": "运费险",
    "四": "保修",
    "五": "退款",
    "六": "FAQ",
}

_CJK = re.compile(r"[一-鿿]")
_ASCII_WORD = re.compile(r"[a-zA-Z0-9]+")
_HEADING = re.compile(r"^(#{3,4})\s+(.+)$")
_SECTION = re.compile(r"^##\s+([一二三四五六])、")
_APPENDIX_START = re.compile(r"^##\s+附录")
# 引用行：**依据**：1.2 价值贬损类；4.1 三包规定 / 详见 2.3 差价处理 / 见 3.5
_REF_LINE = re.compile(r"(?:依据|详见|见|按)\s*[:：]?\s*([1-6]\.\d+|Q\d+)")
_REF_AFTER = re.compile(r"依据[：:]\s*([^\n]+)")
_REF_ID = re.compile(r"[1-6]\.\d+")


@dataclass
class Chunk:
    doc_id: str
    entry_id: str          # "1.2" / "Q03"
    title: str             # 标题（去掉层级符号）
    section: str           # 一/二/...（章节）
    category: str          # 退货/换货/...
    keywords: list[str]
    raw: str               # 标题 + 正文（索引用，含引用行）
    text: str = ""         # 标题 + 正文（展示用，去除 ** 记号）

    @property
    def source_ref(self) -> str:
        return f"{self.entry_id} {self.title}"


def tokenize(text: str) -> list[str]:
    """轻量分词：ASCII 词原样 + 中文单字 + 相邻字符二元组（uni + bi）。

    无 jieba 依赖；对客服短句（口语化实词）命中稳定。tri 实验证明有害，弃用。
    """
    tokens: list[str] = []
    for m in _ASCII_WORD.finditer(text):
        tokens.append(m.group().lower())
    chars = "".join(_CJK.findall(text))
    if not chars:
        return tokens
    if len(chars) == 1:
        tokens.append(chars)
    else:
        tokens.extend(chars)  # uni
        tokens.extend(chars[i : i + 2] for i in range(len(chars) - 1))  # bi
    return tokens


def normalize_query(query: str) -> str:
    """买家口语 -> 政策术语（长词优先替换，确定性、零依赖）。"""
    q = query
    for src, dst in QUERY_NORMALIZE:
        q = q.replace(src, dst)
    return q


def extract_refs(text: str) -> list[str]:
    """从 chunk 原文提取引用目标条目（`**依据**：1.2 …；4.1 …` 等）。

    先去除 `**` 粗体记号，再用逐条 + 行内全量两个正则兜住多条目引用。
    """
    t = text.replace("**", "")
    out: list[str] = []
    for m in _REF_LINE.finditer(t):
        out.append(m.group(1))
    for m in _REF_AFTER.finditer(t):
        out += _REF_ID.findall(m.group(1))
    return sorted(set(out))


def _strip_md(line: str) -> str:
    """去除 markdown 记号（** 粗体 / - 列表），保留语义文本。"""
    return line.replace("**", "").lstrip(" -").strip()


def load_chunks(path: Path | None = None) -> list[Chunk]:
    """解析知识库为自包含 chunk 列表（缓存于模块级）。"""
    path = path or KB_PATH
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    chunks: list[Chunk] = []
    in_frontmatter = True  # 跳过 --- 之间的 YAML 头
    section = ""
    entry_id = ""
    title = ""
    body: list[str] = []

    def flush():
        nonlocal entry_id, title, body
        if entry_id and body:
            raw = "\n".join([f"{entry_id} {title}", *body])
            keywords: list[str] = []
            text_lines: list[str] = []
            for ln in body:
                if ln.startswith("**关键词**"):
                    kw_part = ln.split("**关键词**", 1)[-1].lstrip("：:")
                    keywords = [k.strip() for k in re.split(r"[、，,]", kw_part) if k.strip()]
                else:
                    text_lines.append(_strip_md(ln))
            chunks.append(
                Chunk(
                    doc_id="kb-customer-service",
                    entry_id=entry_id,
                    title=title,
                    section=section,
                    category=_SECTION_CATEGORY.get(section, ""),
                    keywords=keywords,
                    raw=raw,
                    text="\n".join([f"{entry_id} {title}", *text_lines]),
                )
            )
        entry_id, title, body = "", "", []

    for ln in lines:
        if in_frontmatter:
            if ln.strip() == "---":
                in_frontmatter = False
            continue
        if _APPENDIX_START.match(ln):  # 附录 A/B 不参与检索
            break
        m = _SECTION.match(ln)
        if m:
            flush()
            section = m.group(1)
            continue
        h = _HEADING.match(ln)
        if h:
            flush()
            entry_id, title = h.group(2).split(maxsplit=1) if " " in h.group(2) else (h.group(2), h.group(2))
            continue
        if section:
            body.append(ln)
    flush()
    return chunks


class BM25Index:
    """轻量 BM25 检索：字段加权（标题/关键词/正文）+ 近义归一化 + 引用扩展。

    评分：score = Σ_{t∈query} idf[t] × log1p(wt·tf_title + wk·tf_kw + wb·tf_body)
    无长度归一化、无 jieba；确定性强，满足演示/评测需求。
    """

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.n = len(chunks)
        self.by_id = {c.entry_id: c for c in chunks}
        self._doc_tf: list[Counter] = []
        self.idf: dict[str, float] = {}

        df: Counter[str] = Counter()
        for c in chunks:
            t = Counter(tokenize(c.title))
            k = Counter(tokenize(" ".join(c.keywords)))
            b = Counter(tokenize(c.text))
            tf = Counter()
            for tok, cnt in t.items():
                tf[tok] += cnt * WEIGHT_TITLE
            for tok, cnt in k.items():
                tf[tok] += cnt * WEIGHT_KEYWORD
            for tok, cnt in b.items():
                tf[tok] += cnt * WEIGHT_BODY
            self._doc_tf.append(tf)
            # 字段重复 df（与调参实验严格一致）：title/keywords/body 分别计数，
            # 同一词跨字段出现 -> df 增大 -> idf 变小。语义上惩罚跨字段通用实词、
            # 放大单字段独特词（如「没赔」），评测标定即此口径。
            df.update(t.keys())
            df.update(k.keys())
            df.update(b.keys())
        for t, d in df.items():
            self.idf[t] = math.log((self.n - d + 0.5) / (d + 0.5) + 1)

        # 引用图：FAQ chunk -> 其依据条目（仅指向语料内条目）
        self.refs: dict[str, list[str]] = {}
        for c in chunks:
            rs = [r for r in extract_refs(c.raw) if r in self.by_id]
            if rs:
                self.refs[c.entry_id] = rs

    def _raw_score(self, q_tokens: set[str], idx: int) -> float:
        tf = self._doc_tf[idx]
        s = 0.0
        for t in q_tokens:
            f = tf.get(t, 0)
            if f and t in self.idf:
                s += self.idf[t] * math.log1p(f)
        return s

    def search(self, query: str, top_k: int = TOP_K, expand: bool = True) -> list[tuple[Chunk, float]]:
        """检索入口。

        expand=True（默认）：在原始分之上做引用扩展，使「问答 + 依据条目」成对出现。
        expand=False：返回纯 BM25 原始分，供编排层做拒答阈值判定（不受引用提升干扰）。
        """
        q_tokens = set(tokenize(normalize_query(query)))
        if not q_tokens:
            return []
        raw = [(self._raw_score(q_tokens, i), c) for i, c in enumerate(self.chunks)]
        raw.sort(key=lambda x: x[0], reverse=True)
        if not expand:
            return [(c, s) for s, c in raw[:top_k]]

        pool = raw[:REF_POOL]
        ref_src: dict[str, tuple[int, float]] = {}  # 目标 -> (来源排名, boost)
        for rank, (_, c) in enumerate(pool):
            for r in self.refs.get(c.entry_id, []):
                if r not in ref_src or rank < ref_src[r][0]:
                    ref_src[r] = (rank, REF_BOOST)

        raw_score = {c.entry_id: s for s, c in raw}
        final: list[tuple[float, Chunk]] = []
        seen: set[str] = set()
        for s, c in pool:
            bf = ref_src[c.entry_id][1] if c.entry_id in ref_src else 1.0
            final.append((s * bf, c))
            seen.add(c.entry_id)
        for r, (rank, boost) in ref_src.items():
            if r in seen:
                continue
            rb = boost * (1.0 - REF_DECAY * rank)  # 来源排名越靠后衰减越多
            final.append((raw_score[r] * max(1.0, rb), self.by_id[r]))
        final.sort(key=lambda x: x[0], reverse=True)
        return [(c, s) for s, c in final[:top_k]]


_cache_chunks: list[Chunk] | None = None
_cache_index: BM25Index | None = None


def get_corpus() -> list[Chunk]:
    global _cache_chunks
    if _cache_chunks is None:
        _cache_chunks = load_chunks()
    return _cache_chunks


def get_index() -> BM25Index:
    global _cache_index
    if _cache_index is None:
        _cache_index = BM25Index(get_corpus())
    return _cache_index


def search(query: str, top_k: int = TOP_K, expand: bool = True) -> list[tuple[Chunk, float]]:
    """对外检索入口：返回 (chunk, score)，已按分数降序取 top_k。"""
    return get_index().search(query, top_k=top_k, expand=expand)
