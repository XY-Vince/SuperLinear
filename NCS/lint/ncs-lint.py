#!/usr/bin/env python3
"""NCS Protocol v1.2 - Warning-only Static Style & Claim Linter.

Audits text for scripted customer-service phrasing, conversational padding,
servile AI theater, and unbounded/dramatic claims, while respecting literal
physical and syntactic allowances, code fences, and negative contexts.

Usage:
    python3 ncs-lint.py <file1> [file2 ...]
    cat file.md | python3 ncs-lint.py -
    python3 ncs-lint.py --json <file>

Exit codes:
    0: Clean (no warnings or claim reviews)
    1: Warnings or claim reviews detected
    2: File not found or CLI read/usage error
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Punctuation to split a line into clauses for isolated evaluation
CLAUSE_SPLIT = re.compile(r"[；;。!?！？\n]")

# Code fence matcher (handles ``` or ~~~ of 3+ chars)
FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

# Negation preceding check directly attached to match (e.g., "不能保证安全")
NEGATION_PRECEDING = re.compile(r"(?:无法|不能|难以|并非|尚未|没有|未曾|不保证|不代表|不承诺|不宣称|不能保证|无法保证|难以保证|不敢保证|尚未确认|未能保证)\s{0,3}$")

# Critique context markers for quoted bad examples
CRITIQUE_MARKERS = re.compile(r"(?:不成立|不可信|不准确|夸大|虚假|错误|问题|不可取|属于|断言|承诺|反例|评测)")

RULES = [
    # --- Category 1: Style Warnings (Customer-Service & Corporate Fillers) ---
    {
        "id": "zhebian_filler",
        "category": "style_warning",
        "flag_pattern": re.compile(
            r"(?:我|您|系统)?\s*这边.{0,6}?(?:查到|查了|看到|看了|建议|确认|处理|提供|核实|回复|分析|说)"
        ),
        "allow_pattern": re.compile(
            r"(?:[在往到从靠]\s*这边|这边\s*(?:左右|上下|东西南北|前后)|(?:路|河|房间|校区|桌子|门|墙|桥|街|山)\s*这边)"
        ),
        "message": "“这边”疑似用作客服腔/虚假人称填充词。",
        "suggestion": "直接删除“这边”或明确陈述主语/事实（保留原有时态与证据强度）。",
    },
    {
        "id": "nin_zhebian_filler",
        "category": "style_warning",
        "flag_pattern": re.compile(
            r"您这边.{0,6}?(?:可以|方便|需要|是否|有空|要是|如果)"
        ),
        "allow_pattern": None,
        "message": "“您这边”属于典型的客服/销售代词填充。",
        "suggestion": "替换为“你 / 对方”或直接陈述动作（如“方便自提吗？”）。",
    },
    {
        "id": "geidao_filler",
        "category": "style_warning",
        "flag_pattern": re.compile(
            r"给到.{0,8}?(?:建议|方案|支持|反馈|价格|报价|优惠|折扣|回复|信息|解答)"
        ),
        "allow_pattern": re.compile(
            r"给到.{0,8}?(?:前台|前门|手里|车里|桌上|箱子里|家里|现场|仓库)"
        ),
        "message": "“给到”疑似互联网大厂/中介式抽象黑话。",
        "suggestion": "替换为“给出 / 提供 / 报价为 / 建议是”。",
    },
    {
        "id": "bangnin_filler",
        "category": "style_warning",
        # Flags empty announcement of routine actions; allows offers of help ("我可以帮您...")
        "flag_pattern": re.compile(
            r"(?:这边|这就|已经|在此)?\s*帮您\s*(?:查询|查了一下|查了|看了下|核对过了|处理了一下|登记好了)"
        ),
        "allow_pattern": re.compile(
            r"(?:可以|能|方便|愿意|协助|提供|帮您(?:搬|拿|运|送|修|装|抬|提|带|寄))"
        ),
        "message": "“帮您”作为非必要服务动作宣告；若属于提供具体的实际协助或服务选项，可保留。",
        "suggestion": "直接陈述结果或动作，避免服务台式的过程宣告。",
    },
    {
        "id": "zhekuai_filler",
        "category": "style_warning",
        # Flags topical filler "这块的话" or "在...这块", does not flag classifier "这块 + 名词" (e.g. 这块蛋糕)
        "flag_pattern": re.compile(
            r"(?:这块的话|在\S+这块(?:上|方面)?|这块[，,\s]+(?:我们|建议|后续|主要|需要|可以|目前|暂时|先|考虑|来看))"
        ),
        "allow_pattern": re.compile(
            r"这块\s*(?:木板|电池|屏幕|手表|肥皂|抹布|橡皮|地毯|玻璃|铁皮|肉|布料|土地|空地|石头|玉|硬盘|蛋糕|面包|巧克力|场地)"
        ),
        "message": "“这块的话”疑似口语化填充词/大厂黑话。",
        "suggestion": "替换为“关于 X / 在 X 方面”或直接删除。",
    },
    {
        "id": "service_endings",
        "category": "style_warning",
        "flag_pattern": re.compile(
            r"(?:好的呢|可以的哈|没问题的哈|没问题呢|安排上了哈|很高兴为您服务|希望可以帮助到您|希望对您有所帮助)"
        ),
        "allow_pattern": None,
        "message": "检测到淘宝/客服式句尾填充词或表演式结语。",
        "suggestion": "删除或改为正常用语（如“可以。”、“好的。”）。",
    },
    {
        "id": "en_servile_opener",
        "category": "style_warning",
        # Punctuation check handles "Certainly." / "Absolutely!" while avoiding "Absolutely positioned"
        "flag_pattern": re.compile(
            r"^\s*(?:(?:Certainly|Absolutely|Great question)(?:[!.,]+(?:\s|$)|(?:\s*$)|(?:\s+(?:here|I|we|let|you|sure)\b))|(?:I(?:'d| would) be happy to help|Happy to help!?|I(?:'d| would) be glad to assist)\b)",
            re.IGNORECASE,
        ),
        "allow_pattern": None,
        "message": "English servile opener / artificial enthusiasm detected.",
        "suggestion": "Lead directly with the answer or key fact.",
    },

    # --- Category 2: Claim Reviews (Evidence & Proportion Calibration) ---
    {
        "id": "unbounded_success_claim",
        "category": "claim_review",
        "flag_pattern": re.compile(
            r"(?:彻底|全部|全量|完全)(?:消除|解决|修复|搞定|闭环|杜绝|清零|根治)"
        ),
        "allow_pattern": None,
        "message": "请复核该结论的范围与依据。",
        "suggestion": "限定具体修复对象与已验证的边界（例如“已修复本轮列出的 N 处问题；实时链路待验证”）。",
    },
    {
        "id": "unsupported_guarantee",
        "category": "claim_review",
        "flag_pattern": re.compile(
            r"(?:100%|绝对|保证)(?:安全|无误|零风险|不遗漏|没问题|无缺陷|稳定)"
        ),
        "allow_pattern": None,
        "message": "请复核是否有足够证据支持该绝对化承诺。",
        "suggestion": "说明依据的测试/数据范围，避免超出当前验证边界的承诺。",
    },
    {
        "id": "dramatic_or_dismissive_framing",
        "category": "claim_review",
        "flag_pattern": re.compile(
            r"(?:零污染、?零风险|完美闭环|绝对未触碰|毫无价值|唯一正确)"
        ),
        "allow_pattern": None,
        "message": "措辞带有战报化戏剧性或无依据贬低，建议用平静客观的技术事实陈述。",
        "suggestion": "用平静客观的技术事实陈述（如“未修改正式目录”、“比对了目标文件哈希”）。",
    },
]

PRESET_RULES = {
    "coding": [
        {
            "id": "unverified_production_guarantee",
            "category": "claim_review",
            "flag_pattern": re.compile(
                r"(?:直接上线绝无bug|绝对可用|不可能报错|完美运行无瑕疵|毫无瑕疵|零缺陷)"
            ),
            "allow_pattern": None,
            "message": "检测到未经验证的代码上线或系统绝对化保证。",
            "suggestion": "以实际测试、阶段性回归和环境约束为准，限定验证边界。",
        },
    ],
    "marketplace": [
        {
            "id": "superlative_hype",
            "category": "style_warning",
            "flag_pattern": re.compile(
                r"(?:全网最低|绝无仅有|手慢无|不买后悔|宇宙第一|全网最强|先到先得手慢无)"
            ),
            "allow_pattern": None,
            "message": "检测到集市夸大性营销/催促用语。",
            "suggestion": "客观陈述商品成色、配置、价格与自提条件。",
        },
    ],
    "investment": [
        {
            "id": "guaranteed_return",
            "category": "claim_review",
            "flag_pattern": re.compile(
                r"(?:稳赚不赔|必赚|暴利|零风险投资|保本保收益|躺赚)"
            ),
            "allow_pattern": None,
            "message": "检测到投资绝对化承诺，违反投资客观中立原则。",
            "suggestion": "披露核心假设、估值边界与下行风险。",
        },
        {
            "id": "fomo_urgency",
            "category": "style_warning",
            "flag_pattern": re.compile(
                r"(?:赶紧上车|再不买就晚了|错过不再有|闭眼入|重仓杀入)"
            ),
            "allow_pattern": None,
            "message": "检测到煽动性投资话术。",
            "suggestion": "客观陈述投资逻辑、流动性约束与安全边际。",
        },
    ],
    "academic": [
        {
            "id": "speculative_spin",
            "category": "claim_review",
            "flag_pattern": re.compile(
                r"(?:举世无双|空前绝后|颠覆性革命|彻底推翻前人|震古烁今)"
            ),
            "allow_pattern": None,
            "message": "检测到学术论文非客观煽动性措辞。",
            "suggestion": "使用审慎严谨的实证表述，明确本项工作的边界与局限。",
        },
    ],
}

def strip_inline_code(line: str) -> str:
    """Replaces inline code `...` with equivalent spaces to preserve character offsets."""
    def repl(m):
        return " " * len(m.group(0))
    return re.sub(r"`[^`\n]+`", repl, line)

def is_quoted_critique(line: str, match_start: int, match_end: int) -> bool:
    """Checks if a match is enclosed in quotes inside a line that critiques it."""
    before = line[:match_start]
    after = line[match_end:]

    # Check Chinese quotes “...”
    last_open_cn = before.rfind("“")
    last_close_cn = before.rfind("”")
    if last_open_cn != -1 and (last_close_cn == -1 or last_open_cn > last_close_cn):
        if after.find("”") != -1 and CRITIQUE_MARKERS.search(line):
            return True

    # Check English quotes "..."
    quote_count_before = before.count('"')
    quote_count_after = after.count('"')
    if quote_count_before % 2 == 1 and quote_count_after % 2 == 1 and CRITIQUE_MARKERS.search(line):
        return True

    return False

def audit_text(text: str, source_name: str = "<input>", preset: str | None = None):
    warnings = []
    lines = text.splitlines()
    fence_marker = None

    active_rules = list(RULES)
    if preset and preset.lower() in PRESET_RULES:
        active_rules.extend(PRESET_RULES[preset.lower()])

    for line_idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Handle fenced code block toggle (supports ``` and ~~~ of 3+ chars)
        fence_match = FENCE_RE.match(stripped)
        if fence_marker is None:
            if fence_match:
                fence_marker = fence_match.group(1)
                continue
        else:
            if stripped.startswith(fence_marker):
                fence_marker = None
                continue
            continue

        if not stripped:
            continue

        clean_line = strip_inline_code(line)

        # Split line into clauses with start offsets so allow_pattern in clause 1 does not suppress flag in clause 2,
        # and so match coordinates can be projected back to line-level coordinates for quoted-critique detection.
        clauses = []
        last_end = 0
        for m in CLAUSE_SPLIT.finditer(clean_line):
            c_text = clean_line[last_end:m.start()]
            if c_text.strip():
                clauses.append((c_text, last_end))
            last_end = m.end()
        if last_end < len(clean_line):
            c_text = clean_line[last_end:]
            if c_text.strip():
                clauses.append((c_text, last_end))
        if not clauses:
            clauses = [(clean_line, 0)]

        for clause, c_start in clauses:
            clause_stripped = clause.strip()
            if not clause_stripped:
                continue

            for rule in active_rules:
                # Use finditer to catch ALL occurrences in the clause
                for flag_match in rule["flag_pattern"].finditer(clause):
                    m_start = flag_match.start()
                    m_end = flag_match.end()

                    # 1. Check local allowance on the clause
                    if rule["allow_pattern"] and rule["allow_pattern"].search(clause):
                        continue

                    # 2. Check local negation immediately preceding this specific match
                    if rule["category"] == "claim_review":
                        preceding_clause_text = clause[:m_start]
                        if NEGATION_PRECEDING.search(preceding_clause_text):
                            continue

                    # 3. Check if match is inside a quoted critique (using line-level offsets)
                    line_start = c_start + m_start
                    line_end = c_start + m_end
                    if is_quoted_critique(line, line_start, line_end):
                        continue

                    matched_snippet = flag_match.group(0)
                    warnings.append({
                        "source": source_name,
                        "line": line_idx,
                        "rule": rule["id"],
                        "category": rule["category"],
                        "matched": matched_snippet,
                        "clause_text": clause_stripped,
                        "line_text": stripped,
                        "message": rule["message"],
                        "suggestion": rule["suggestion"],
                    })

    return warnings

def main():
    parser = argparse.ArgumentParser(description="NCS Protocol v1.2 Warning-only Style & Claim Linter")
    parser.add_argument("targets", nargs="*", help="File path(s) or - for stdin")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument(
        "--preset",
        choices=list(PRESET_RULES.keys()),
        default=None,
        help="Activate domain-specific preset rules (coding, marketplace, investment, academic)",
    )
    parser.add_argument("--list-presets", action="store_true", help="List available context presets and exit")
    args = parser.parse_args()

    if args.list_presets:
        print("Available NCS Context Presets:")
        for name, rules in PRESET_RULES.items():
            print(f"  - {name}: {len(rules)} additional domain rules (see presets/{name}.md)")
        sys.exit(0)

    if not args.targets:
        parser.print_help()
        sys.exit(2)

    all_warnings = []
    has_read_error = False

    for target in args.targets:
        if target == "-":
            text = sys.stdin.read()
            all_warnings.extend(audit_text(text, source_name="<stdin>", preset=args.preset))
        else:
            p = Path(target)
            if not p.exists():
                sys.stderr.write(f"Error: target not found: {target}\n")
                has_read_error = True
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                all_warnings.extend(audit_text(text, source_name=str(p), preset=args.preset))
            except Exception as e:
                sys.stderr.write(f"Error reading {target}: {e}\n")
                has_read_error = True

    # If any target could not be read, exit code 2 takes priority
    if has_read_error:
        sys.exit(2)

    if args.json:
        print(json.dumps({"warnings": all_warnings, "total": len(all_warnings)}, ensure_ascii=False, indent=2))
    else:
        if not all_warnings:
            print("✓ NCS Style Lint: 未命中已配置规则（不代表事实已核验）。")
        else:
            style_count = sum(1 for w in all_warnings if w["category"] == "style_warning")
            claim_count = sum(1 for w in all_warnings if w["category"] == "claim_review")
            print(f"⚠ NCS Style Lint: 发现 {len(all_warnings)} 项提醒 ({style_count} 项风格提示, {claim_count} 项声明复核):\n")
            for w in all_warnings:
                tag = "STYLE" if w["category"] == "style_warning" else "CLAIM"
                print(f"[{w['source']}:{w['line']}] [{tag}:{w['rule']}] 匹配: \"{w['matched']}\"")
                print(f"  分句: {w['clause_text']}")
                print(f"  提示: {w['message']}")
                print(f"  建议: {w['suggestion']}\n")

    sys.exit(1 if all_warnings else 0)

if __name__ == "__main__":
    main()
