"""Static code-style screening for user-provided source text.

This module intentionally does not fetch Codeforces submission source. The
official CF API exposes submission metadata, not source code bodies, so the Web
UI passes pasted/uploaded code here for local heuristic analysis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from schemas import CodeStyleIssue, CodeStyleReport


_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_NUMBER_RE = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?![A-Za-z_])")
_CPP_FUNCTION_RE = re.compile(
    r"^\s*(?:template\s*<[^>]+>\s*)?"
    r"(?:[\w:<>,~*&\[\]\s]+\s+)+"
    r"([A-Za-z_][A-Za-z0-9_:]*)\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept\s*)?\{?\s*$"
)
_PY_FUNCTION_RE = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")


@dataclass
class _FunctionSpan:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def _guess_language(code: str, filename: str | None) -> str:
    name = (filename or "").lower()
    if name.endswith((".cpp", ".cc", ".cxx", ".hpp", ".h")):
        return "cpp"
    if name.endswith(".py"):
        return "python"
    if name.endswith(".java"):
        return "java"
    if "#include" in code or "using namespace std" in code or "int main(" in code:
        return "cpp"
    if re.search(r"^\s*def\s+\w+\(", code, re.M):
        return "python"
    if "public class " in code or "static void main" in code:
        return "java"
    return "unknown"


def _strip_strings_and_comments(line: str) -> str:
    line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
    line = re.sub(r"'(?:\\.|[^'\\])*'", "''", line)
    line = re.sub(r"//.*$", "", line)
    line = re.sub(r"#.*$", "", line)
    return line


def _comment_line_count(lines: list[str], language: str) -> int:
    count = 0
    in_block = False
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if in_block:
            count += 1
            if "*/" in line:
                in_block = False
            continue
        if language == "python" and line.startswith("#"):
            count += 1
            continue
        if line.startswith("//"):
            count += 1
            continue
        if line.startswith("/*"):
            count += 1
            if "*/" not in line:
                in_block = True
    return count


def _brace_nesting(lines: list[str]) -> int:
    depth = 0
    max_depth = 0
    for raw in lines:
        line = _strip_strings_and_comments(raw)
        for ch in line:
            if ch == "{":
                depth += 1
                max_depth = max(max_depth, depth)
            elif ch == "}":
                depth = max(0, depth - 1)
    return max_depth


def _python_nesting(lines: list[str]) -> int:
    indents: list[int] = []
    max_depth = 0
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        spaces = len(raw) - len(raw.lstrip(" "))
        while indents and spaces <= indents[-1]:
            indents.pop()
        if raw.rstrip().endswith(":"):
            indents.append(spaces)
            max_depth = max(max_depth, len(indents))
    return max_depth


def _cpp_function_spans(lines: list[str]) -> list[_FunctionSpan]:
    spans: list[_FunctionSpan] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not _CPP_FUNCTION_RE.match(line) or any(
            token in line for token in (" if ", " for ", " while ", " switch ")
        ):
            i += 1
            continue
        brace_depth = line.count("{") - line.count("}")
        start = i
        j = i
        if brace_depth <= 0:
            j += 1
            while j < len(lines) and "{" not in lines[j]:
                j += 1
            if j >= len(lines):
                i += 1
                continue
            brace_depth = lines[j].count("{") - lines[j].count("}")
        while j + 1 < len(lines) and brace_depth > 0:
            j += 1
            brace_depth += lines[j].count("{") - lines[j].count("}")
        spans.append(_FunctionSpan(start=start + 1, end=j + 1))
        i = max(i + 1, j + 1)
    return spans


def _python_function_spans(lines: list[str]) -> list[_FunctionSpan]:
    starts: list[tuple[int, int]] = []
    for idx, raw in enumerate(lines):
        if _PY_FUNCTION_RE.match(raw):
            starts.append((idx, len(raw) - len(raw.lstrip(" "))))
    spans: list[_FunctionSpan] = []
    for pos, (idx, indent) in enumerate(starts):
        end = len(lines) - 1
        for j in range(idx + 1, len(lines)):
            raw = lines[j]
            if not raw.strip():
                continue
            current_indent = len(raw) - len(raw.lstrip(" "))
            if current_indent <= indent and not raw.lstrip().startswith("#"):
                end = j - 1
                break
        if pos + 1 < len(starts):
            end = min(end, starts[pos + 1][0] - 1)
        spans.append(_FunctionSpan(start=idx + 1, end=end + 1))
    return spans


def _function_spans(lines: list[str], language: str) -> list[_FunctionSpan]:
    if language == "python":
        return _python_function_spans(lines)
    if language in {"cpp", "java", "unknown"}:
        return _cpp_function_spans(lines)
    return []


def _macro_count(lines: list[str]) -> int:
    return sum(1 for line in lines if line.lstrip().startswith("#define"))


def _global_mutable_count(lines: list[str], language: str) -> int:
    if language not in {"cpp", "java", "unknown"}:
        return 0
    count = 0
    depth = 0
    for raw in lines:
        line = _strip_strings_and_comments(raw).strip()
        if not line:
            depth += raw.count("{") - raw.count("}")
            depth = max(0, depth)
            continue
        if depth == 0 and line.endswith(";"):
            if not line.startswith(("#", "using ", "typedef ", "return ")):
                if not any(token in line for token in ("const ", "constexpr", "struct ", "class ", "enum ")):
                    if re.search(r"\b(int|long|double|float|char|bool|string|vector|array|map|set|queue|stack)\b", line):
                        count += 1
        depth += raw.count("{") - raw.count("}")
        depth = max(0, depth)
    return count


def _identifier_stats(code: str) -> tuple[int, int, int, int]:
    keywords = {
        "int", "long", "double", "float", "char", "bool", "string", "return",
        "if", "else", "for", "while", "switch", "case", "break", "continue",
        "class", "struct", "public", "private", "protected", "void", "auto",
        "const", "static", "def", "import", "from", "in", "and", "or", "not",
        "True", "False", "None", "vector", "map", "set", "queue", "stack",
    }
    identifiers = [x for x in _IDENT_RE.findall(code) if x not in keywords]
    if not identifiers:
        return 0, 0, 0, 0
    short = sum(1 for x in identifiers if len(x) <= 2)
    snake = sum(1 for x in identifiers if "_" in x and x.lower() == x)
    camel = sum(1 for x in identifiers if re.search(r"[a-z][A-Z]", x))
    return len(identifiers), short, snake, camel


def _magic_number_count(lines: list[str]) -> int:
    allowed = {"-1", "0", "1", "2", "10", "100", "1000"}
    count = 0
    for raw in lines:
        line = _strip_strings_and_comments(raw)
        for match in _NUMBER_RE.findall(line):
            if match not in allowed:
                count += 1
    return count


def _add_issue(
    issues: list[CodeStyleIssue],
    severity: str,
    category: str,
    title: str,
    detail: str,
) -> None:
    issues.append(CodeStyleIssue(
        severity=severity, category=category, title=title, detail=detail,
    ))


def analyze_code_style(code: str, filename: str | None = None) -> CodeStyleReport:
    normalized = code.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = normalized.splitlines() if normalized else []
    language = _guess_language(normalized, filename)
    nonempty = [line for line in lines if line.strip()]
    total = len(lines)
    nonempty_count = len(nonempty)

    line_lengths = [len(line.expandtabs(4)) for line in lines]
    avg_line = round(sum(line_lengths) / total, 1) if total else 0.0
    max_line = max(line_lengths, default=0)
    long_lines = sum(1 for n in line_lengths if n > 100)

    comments = _comment_line_count(lines, language)
    comment_ratio = round(comments / nonempty_count, 3) if nonempty_count else 0.0

    spans = _function_spans(lines, language)
    function_lengths = [span.length for span in spans]
    avg_func = round(sum(function_lengths) / len(function_lengths), 1) if function_lengths else 0.0
    max_func = max(function_lengths, default=0)

    max_nesting = _python_nesting(lines) if language == "python" else _brace_nesting(lines)
    macros = _macro_count(lines)
    globals_count = _global_mutable_count(lines, language)
    magic_numbers = _magic_number_count(lines)
    ident_total, short_ident, snake_ident, camel_ident = _identifier_stats(normalized)
    short_ratio = round(short_ident / ident_total, 3) if ident_total else 0.0

    score = 100.0
    issues: list[CodeStyleIssue] = []

    if not normalized.strip():
        _add_issue(issues, "risk", "input", "没有源码", "请粘贴一段完整代码后再筛查。")
        score = 0.0
    if long_lines:
        penalty = min(18, long_lines * 2)
        score -= penalty
        _add_issue(
            issues, "warn" if long_lines <= 5 else "risk",
            "line_length",
            "长行偏多",
            f"{long_lines} 行超过 100 字符，最长 {max_line} 字符。",
        )
    if max_func > 120 or avg_func > 70:
        score -= 14
        _add_issue(
            issues, "risk",
            "function_size",
            "函数粒度过大",
            f"最大函数约 {max_func} 行，平均函数约 {avg_func} 行。",
        )
    elif max_func > 70 or avg_func > 45:
        score -= 8
        _add_issue(
            issues, "warn",
            "function_size",
            "函数略长",
            f"最大函数约 {max_func} 行，建议拆出读入、转移、检查或输出逻辑。",
        )
    if max_nesting > 5:
        score -= min(16, (max_nesting - 5) * 5)
        _add_issue(
            issues, "risk",
            "nesting",
            "嵌套层级偏深",
            f"最大嵌套层级约 {max_nesting}，读题和调试时容易漏掉分支。",
        )
    elif max_nesting > 3:
        score -= 6
        _add_issue(
            issues, "warn",
            "nesting",
            "存在较深嵌套",
            f"最大嵌套层级约 {max_nesting}，可考虑提前 continue/return 或抽函数。",
        )
    if macros > 8:
        score -= 10
        _add_issue(
            issues, "warn",
            "macros",
            "宏使用偏多",
            f"检测到 {macros} 个 #define，宏会降低可调试性和类型安全。",
        )
    if globals_count > 8:
        score -= 10
        _add_issue(
            issues, "warn",
            "globals",
            "全局可变状态偏多",
            f"疑似全局可变变量 {globals_count} 个，容易在多测和复用时残留状态。",
        )
    if nonempty_count >= 30 and comment_ratio < 0.025:
        score -= 6
        _add_issue(
            issues, "warn",
            "comments",
            "关键说明偏少",
            f"非空行 {nonempty_count} 行，注释行占比约 {comment_ratio:.1%}。",
        )
    if magic_numbers > max(8, nonempty_count // 8):
        score -= 7
        _add_issue(
            issues, "warn",
            "constants",
            "魔法数偏多",
            f"检测到 {magic_numbers} 个非常见数字，建议把模数、无穷大、边界常量命名。",
        )
    if ident_total >= 30 and short_ratio > 0.45:
        score -= 7
        _add_issue(
            issues, "warn",
            "naming",
            "短变量名占比偏高",
            f"短标识符占比约 {short_ratio:.0%}，复杂逻辑里会降低复盘效率。",
        )

    score = round(max(0.0, min(100.0, score)), 1)
    tags: list[str] = []
    if score >= 85:
        tags.append("结构清爽")
    elif score >= 70:
        tags.append("可维护")
    elif score >= 50:
        tags.append("需要整理")
    else:
        tags.append("高返工风险")
    if macros > 8:
        tags.append("宏较多")
    if max_nesting > 3:
        tags.append("嵌套偏深")
    if long_lines:
        tags.append("长行")
    if globals_count > 8:
        tags.append("全局状态")
    if snake_ident > camel_ident and snake_ident > 0:
        tags.append("snake_case")
    elif camel_ident > 0:
        tags.append("camelCase")

    recommendations: list[str] = []
    if long_lines:
        recommendations.append("把长表达式拆成带语义的中间变量，降低 WA 时定位成本。")
    if max_func > 70:
        recommendations.append("把主函数中的读入、预处理、核心转移和输出拆成小函数。")
    if max_nesting > 3:
        recommendations.append("用 guard clause、continue 或局部函数压平深层 if/for。")
    if macros > 8:
        recommendations.append("保留少量竞赛常用宏，把复杂宏替换为 inline 函数或 using。")
    if globals_count > 8:
        recommendations.append("多测题把可变全局状态集中 reset，或封装进 solve() 的局部结构。")
    if not recommendations and normalized.strip():
        recommendations.append("整体风格较稳定，可以继续保持固定模板和局部命名习惯。")

    summary = (
        f"检测到 {nonempty_count} 行有效代码，风格分 {score:.1f}。"
        f"主要信号：最长行 {max_line}、最大函数 {max_func} 行、最大嵌套 {max_nesting} 层。"
    )

    metrics: dict[str, float | int | str] = {
        "lines": total,
        "nonempty_lines": nonempty_count,
        "avg_line_length": avg_line,
        "max_line_length": max_line,
        "long_lines": long_lines,
        "comment_ratio": comment_ratio,
        "function_count": len(spans),
        "avg_function_lines": avg_func,
        "max_function_lines": max_func,
        "max_nesting": max_nesting,
        "macro_count": macros,
        "global_mutable_count": globals_count,
        "magic_number_count": magic_numbers,
        "short_identifier_ratio": short_ratio,
    }

    return CodeStyleReport(
        language=language,
        score=score,
        summary=summary,
        style_tags=tags,
        metrics=metrics,
        issues=issues,
        recommendations=recommendations,
    )
