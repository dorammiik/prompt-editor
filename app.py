import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from werkzeug.middleware.proxy_fix import ProxyFix


BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "prompts"


def load_prompt(relative_path: str, *, strip: bool = True):
    text = (PROMPTS_DIR / relative_path).read_text(encoding="utf-8")
    return text.strip() if strip else text


FIELD_NAMES = ("improvement", "og_prompt", "og_intro", "og_chatHistory")
MAX_FIELD_LENGTH = 100_000
MAX_TOTAL_LENGTH = 250_000
MAX_REQUIRED_PROMPTS_LENGTH = 30_000
MAX_COMPILE_TOTAL_LENGTH = 300_000
MAX_TEST_TOTAL_LENGTH = 500_000
OPENAI_DAILY_CALL_LIMIT = 15
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
SUPPORTED_OUTPUT_LANGUAGES = {"ko", "en"}
COMPILATION_INSTRUCTIONS = load_prompt("03_final_prompt_compilation.txt")
TEST_GENERATION_INSTRUCTIONS = load_prompt("04_test_conversation_generation.txt")
EVALUATION_INSTRUCTIONS = load_prompt("05_ab_test_evaluation.txt")

DIAGNOSIS_INSTRUCTIONS = load_prompt("01_problem_diagnosis.txt")

IMPROVEMENT_INSTRUCTIONS = load_prompt("02_prompt_improvement.txt")

INPUT_TEMPLATES = {
    "01_problem_diagnosis": load_prompt("input_templates/01_problem_diagnosis.txt", strip=False),
    "02_prompt_improvement": load_prompt("input_templates/02_prompt_improvement.txt", strip=False),
    "03_final_prompt_compilation": load_prompt("input_templates/03_final_prompt_compilation.txt", strip=False),
    "04_test_conversation_generation": load_prompt("input_templates/04_test_conversation_generation.txt", strip=False),
    "05_ab_test_evaluation": load_prompt("input_templates/05_ab_test_evaluation.txt", strip=False),
}

DIAGNOSIS_SECTION_KEYS = {
    "개선 대상": "improvement_targets",
    "문제 현상": "problem_symptoms",
    "개선 목표": "improvement_goals",
    "금지 사항": "prohibited_patterns",
    "원본 프롬프트 문제 원인": "prompt_causes",
}

OUTPUT_LANGUAGE_CONTRACTS = {
    language: load_prompt(f"output_languages/{language}.txt")
    for language in SUPPORTED_OUTPUT_LANGUAGES
}

ERROR_CODE_EXACT = {
    "JSON 형식으로 요청해 주세요.": "INVALID_JSON",
    "요청 데이터 형식이 올바르지 않아요.": "INVALID_REQUEST",
    "지원하지 않는 출력 언어예요.": "INVALID_OUTPUT_LANGUAGE",
    "하루 이용 횟수를 초과하였습니다.": "DAILY_LIMIT_EXCEEDED",
    "서버에서 예상하지 못한 오류가 발생했어요.": "INTERNAL_ERROR",
}


app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
# The production server runs behind one Nginx proxy. This makes
# request.remote_addr resolve to the original client IP in that setup.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

logging.basicConfig(level=logging.INFO)

_openai_call_counts = {}
_openai_call_counts_date = None
_openai_call_counts_lock = threading.Lock()


class OpenAIDailyCallLimitExceeded(Exception):
    pass


def infer_error_code(message: str, status_code: int):
    exact_code = ERROR_CODE_EXACT.get(message)
    if exact_code:
        return exact_code
    if "OPENAI_API_KEY" in message:
        return "API_KEY_MISSING"
    if "인증할 수 없어요" in message:
        return "OPENAI_AUTH_FAILED"
    if "사용 한도" in message or "요청이 많" in message:
        return "OPENAI_RATE_LIMIT"
    if "연결할 수 없어요" in message:
        return "OPENAI_CONNECTION_FAILED"
    if "OpenAI에서" in message:
        return "OPENAI_API_FAILED"
    if "응답이 비어" in message:
        return "EMPTY_MODEL_RESPONSE"
    if "형식을 읽을 수 없어요" in message or "구분할 수 없어요" in message:
        return "INVALID_MODEL_FORMAT"
    if "진단 결과" in message:
        return "INVALID_DIAGNOSIS"
    if "대화 3개" in message:
        return "INVALID_TESTS"
    if "너무 길" in message or status_code == 413:
        return "TOTAL_TOO_LONG" if "전체" in message or status_code == 413 else "FIELD_TOO_LONG"
    if "입력" in message or "작성해 주세요" in message:
        return "REQUIRED_FIELD"
    if status_code >= 500:
        return "OPENAI_REQUEST_FAILED"
    return "INVALID_REQUEST"


def error_response(message: str, status_code: int, code: str | None = None):
    return jsonify(
        {
            "code": code or infer_error_code(message, status_code),
            "error": message,
        }
    ), status_code


def validate_output_language(payload):
    output_language = payload.get("output_language", "ko")
    if not isinstance(output_language, str):
        return None, "지원하지 않는 출력 언어예요."

    output_language = output_language.strip().lower()
    if output_language not in SUPPORTED_OUTPUT_LANGUAGES:
        return None, "지원하지 않는 출력 언어예요."

    return output_language, None


def instructions_for(base_instructions: str, output_language: str):
    return (
        f"{base_instructions}\n\n---\n\n"
        f"{OUTPUT_LANGUAGE_CONTRACTS[output_language]}"
    )


def consume_openai_call():
    global _openai_call_counts_date

    today = datetime.now(SEOUL_TIMEZONE).date()
    client_ip = request.remote_addr or "unknown"

    with _openai_call_counts_lock:
        if _openai_call_counts_date != today:
            _openai_call_counts.clear()
            _openai_call_counts_date = today

        call_count = _openai_call_counts.get(client_ip, 0)
        if call_count >= OPENAI_DAILY_CALL_LIMIT:
            raise OpenAIDailyCallLimitExceeded

        _openai_call_counts[client_ip] = call_count + 1


def create_openai_response(client, **kwargs):
    consume_openai_call()
    return client.responses.create(**kwargs)


def validate_payload(payload):
    if not isinstance(payload, dict):
        return None, "요청 데이터 형식이 올바르지 않아요."

    output_language, language_error = validate_output_language(payload)
    if language_error:
        return None, language_error

    cleaned = {"output_language": output_language}
    missing = []

    for field_name in FIELD_NAMES:
        value = payload.get(field_name)

        if not isinstance(value, str) or not value.strip():
            missing.append(field_name)
            continue

        value = value.strip()
        if len(value) > MAX_FIELD_LENGTH:
            return None, f"{field_name} 입력 내용이 너무 길어요."

        cleaned[field_name] = value

    if missing:
        return None, f"다음 입력 항목을 모두 작성해 주세요: {', '.join(missing)}"

    if sum(
        len(cleaned[field_name])
        for field_name in FIELD_NAMES
    ) > MAX_TOTAL_LENGTH:
        return None, "전체 입력 내용이 너무 길어요. 내용을 줄인 뒤 다시 시도해 주세요."

    return cleaned, None


def validate_compile_payload(payload):
    if not isinstance(payload, dict):
        return None, "요청 데이터 형식이 올바르지 않아요."

    output_language, language_error = validate_output_language(payload)
    if language_error:
        return None, language_error

    text_limits = {
        "improvement": MAX_FIELD_LENGTH,
        "improved_prompt": MAX_FIELD_LENGTH,
        "improved_intro": MAX_FIELD_LENGTH,
        "required_prompts": MAX_REQUIRED_PROMPTS_LENGTH,
    }
    cleaned = {"output_language": output_language}

    for field_name, length_limit in text_limits.items():
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return None, f"{field_name} 입력 내용을 확인해 주세요."

        value = value.strip()
        if len(value) > length_limit:
            return None, f"{field_name} 입력 내용이 너무 길어요."

        cleaned[field_name] = value

    diagnosis_payload = payload.get("diagnosis")
    if not isinstance(diagnosis_payload, dict):
        return None, "문제 진단 결과 형식이 올바르지 않아요."

    cleaned_diagnosis = {}
    for diagnosis_key in DIAGNOSIS_SECTION_KEYS.values():
        items = diagnosis_payload.get(diagnosis_key)
        if not isinstance(items, list) or not items:
            return None, f"{diagnosis_key} 진단 결과를 확인해 주세요."

        cleaned_items = []
        for item in items:
            if not isinstance(item, str) or not item.strip():
                return None, f"{diagnosis_key} 진단 결과를 확인해 주세요."
            cleaned_items.append(item.strip())

        cleaned_diagnosis[diagnosis_key] = cleaned_items

    total_length = sum(
        len(cleaned[field_name])
        for field_name in text_limits
    )
    total_length += sum(
        len(item)
        for items in cleaned_diagnosis.values()
        for item in items
    )
    if total_length > MAX_COMPILE_TOTAL_LENGTH:
        return None, "컴파일할 전체 내용이 너무 길어요. 내용을 줄인 뒤 다시 시도해 주세요."

    cleaned["diagnosis"] = cleaned_diagnosis
    return cleaned, None


def validate_test_payload(payload):
    if not isinstance(payload, dict):
        return None, "요청 데이터 형식이 올바르지 않아요."

    output_language, language_error = validate_output_language(payload)
    if language_error:
        return None, language_error

    required_fields = (
        "improvement",
        "og_prompt",
        "og_intro",
        "final_prompt",
        "final_intro",
        "test_user_input",
    )
    cleaned = {"output_language": output_language}

    for field_name in required_fields:
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip():
            return None, f"{field_name} 입력 내용을 확인해 주세요."

        value = value.strip()
        if len(value) > MAX_FIELD_LENGTH:
            return None, f"{field_name} 입력 내용이 너무 길어요."

        cleaned[field_name] = value

    test_chat_context = payload.get("test_chat_context", "")
    if not isinstance(test_chat_context, str):
        return None, "test_chat_context 입력 내용을 확인해 주세요."

    test_chat_context = test_chat_context.strip()
    if len(test_chat_context) > MAX_FIELD_LENGTH:
        return None, "test_chat_context 입력 내용이 너무 길어요."

    cleaned["test_chat_context"] = test_chat_context

    if sum(
        len(value)
        for key, value in cleaned.items()
        if key != "output_language"
    ) > MAX_TEST_TOTAL_LENGTH:
        return None, "테스트할 전체 내용이 너무 길어요. 내용을 줄인 뒤 다시 시도해 주세요."

    return cleaned, None


def validate_evaluation_payload(payload):
    if not isinstance(payload, dict):
        return None, "요청 데이터 형식이 올바르지 않아요."

    output_language, language_error = validate_output_language(payload)
    if language_error:
        return None, language_error

    improvement = payload.get("improvement")
    if not isinstance(improvement, str) or not improvement.strip():
        return None, "improvement 입력 내용을 확인해 주세요."

    improvement = improvement.strip()
    if len(improvement) > MAX_FIELD_LENGTH:
        return None, "improvement 입력 내용이 너무 길어요."

    cleaned = {
        "improvement": improvement,
        "output_language": output_language,
    }
    for list_key in ("improvement_goals", "prohibited_patterns"):
        values = payload.get(list_key)
        if not isinstance(values, list) or not values:
            return None, f"{list_key} 입력 내용을 확인해 주세요."

        cleaned_values = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                return None, f"{list_key} 입력 내용을 확인해 주세요."
            cleaned_values.append(value.strip())
        cleaned[list_key] = cleaned_values

    for group_key in ("original_tests", "improved_tests"):
        tests = payload.get(group_key)
        if not isinstance(tests, list) or len(tests) != 3:
            return None, f"{group_key} 대화 3개를 모두 확인해 주세요."

        cleaned_tests = []
        for test in tests:
            if not isinstance(test, str) or not test.strip():
                return None, f"{group_key} 대화 3개를 모두 확인해 주세요."
            test = test.strip()
            if len(test) > MAX_FIELD_LENGTH:
                return None, f"{group_key} 대화 내용이 너무 길어요."
            cleaned_tests.append(test)
        cleaned[group_key] = cleaned_tests

    total_length = len(cleaned["improvement"])
    total_length += sum(len(value) for value in cleaned["improvement_goals"])
    total_length += sum(len(value) for value in cleaned["prohibited_patterns"])
    total_length += sum(len(value) for value in cleaned["original_tests"])
    total_length += sum(len(value) for value in cleaned["improved_tests"])
    if total_length > MAX_TEST_TOTAL_LENGTH:
        return None, "평가할 전체 내용이 너무 길어요. 내용을 줄인 뒤 다시 시도해 주세요."

    return cleaned, None


def parse_diagnosis(result_text: str):
    parsed = {key: [] for key in DIAGNOSIS_SECTION_KEYS.values()}
    current_key = None
    heading_pattern = re.compile(
        r"^#{2,4}\s*("
        + "|".join(re.escape(title) for title in DIAGNOSIS_SECTION_KEYS)
        + r")\s*$"
    )

    for raw_line in result_text.splitlines():
        line = raw_line.strip()
        heading_match = heading_pattern.match(line)

        if heading_match:
            current_key = DIAGNOSIS_SECTION_KEYS[heading_match.group(1)]
            continue

        if line.startswith("#"):
            current_key = None
            continue

        if not current_key or not line:
            continue

        bullet_match = re.match(r"^(?:[-*+]|\d+[.)])\s+(.+)$", line)
        if bullet_match:
            item = bullet_match.group(1).strip()
            if item and not (item.startswith("[") and item.endswith("]")):
                parsed[current_key].append(item)
        elif parsed[current_key]:
            parsed[current_key][-1] = f"{parsed[current_key][-1]} {line}"

    return parsed


def has_complete_diagnosis(diagnosis):
    return all(diagnosis.get(key) for key in DIAGNOSIS_SECTION_KEYS.values())


def format_diagnosis_input(diagnosis, user_material):
    def bullets(key):
        return "\n".join(f"- {item}" for item in diagnosis[key])

    return INPUT_TEMPLATES["02_prompt_improvement"].format(
        improvement_targets=bullets("improvement_targets"),
        problem_symptoms=bullets("problem_symptoms"),
        improvement_goals=bullets("improvement_goals"),
        prohibited_patterns=bullets("prohibited_patterns"),
        prompt_causes=bullets("prompt_causes"),
        improvement=user_material["improvement"],
        og_prompt=user_material["og_prompt"],
        og_intro=user_material["og_intro"],
        og_chatHistory=user_material["og_chatHistory"],
    ).strip()


def parse_improvement_result(result_text: str):
    match = re.search(
        r"^#\s*개선 프롬프트\s*\n(.*?)^#\s*개선 인트로 대화\s*\n(.*)$",
        result_text.strip(),
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return "", ""

    return match.group(1).strip(), match.group(2).strip()


def format_compilation_input(compile_material):
    diagnosis = compile_material["diagnosis"]

    def bullets(key):
        return "\n".join(f"- {item}" for item in diagnosis[key])

    return INPUT_TEMPLATES["03_final_prompt_compilation"].format(
        improvement_targets=bullets("improvement_targets"),
        problem_symptoms=bullets("problem_symptoms"),
        improvement_goals=bullets("improvement_goals"),
        prohibited_patterns=bullets("prohibited_patterns"),
        prompt_causes=bullets("prompt_causes"),
        improvement=compile_material["improvement"],
        improved_prompt=compile_material["improved_prompt"],
        improved_intro=compile_material["improved_intro"],
        required_prompts=compile_material["required_prompts"],
    ).strip()


def parse_compilation_result(result_text: str):
    match = re.search(
        r"^#\s*최종 개선 프롬프트\s*\n(.*?)"
        r"^#\s*최종 개선 인트로 대화\s*\n(.*)$",
        result_text.strip(),
        flags=re.MULTILINE | re.DOTALL,
    )
    if not match:
        return "", ""

    return match.group(1).strip(), match.group(2).strip()


def format_test_generation_input(test_material):
    return INPUT_TEMPLATES["04_test_conversation_generation"].format(
        improvement=test_material["improvement"],
        og_prompt=test_material["og_prompt"],
        og_intro=test_material["og_intro"],
        final_prompt=test_material["final_prompt"],
        final_intro=test_material["final_intro"],
        test_chat_context=test_material["test_chat_context"],
        test_user_input=test_material["test_user_input"],
    ).strip()


def has_valid_test_result(result_text: str):
    required_headings = (
        "# 테스트 응답 생성 결과",
        "## 원본 프롬프트 테스트",
        "### 원본 테스트 대화 1",
        "### 원본 테스트 대화 2",
        "### 원본 테스트 대화 3",
        "## 개선 프롬프트 테스트",
        "### 개선 테스트 대화 1",
        "### 개선 테스트 대화 2",
        "### 개선 테스트 대화 3",
    )
    return all(heading in result_text for heading in required_headings)


def parse_generated_tests(result_text: str):
    heading_pattern = re.compile(
        r"^###\s*(원본|개선)\s+테스트 대화\s+([123])\s*$",
        flags=re.MULTILINE,
    )
    matches = list(heading_pattern.finditer(result_text))
    parsed = {"original_tests": ["", "", ""], "improved_tests": ["", "", ""]}

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(result_text)
        content_lines = []

        for line in result_text[start:end].strip().splitlines():
            stripped = line.strip()
            if stripped in {
                "=====",
                "---",
                "--------",
                "## 원본 프롬프트 테스트",
                "## 개선 프롬프트 테스트",
            }:
                continue
            content_lines.append(line)

        content = "\n".join(content_lines).strip()
        group_key = "original_tests" if match.group(1) == "원본" else "improved_tests"
        parsed[group_key][int(match.group(2)) - 1] = content

    if not all(parsed["original_tests"]) or not all(parsed["improved_tests"]):
        return None

    return parsed


def format_evaluation_input(evaluation_material):
    def bullets(key):
        return "\n".join(f"- {item}" for item in evaluation_material[key])

    def tests(key, title_prefix):
        return "\n\n".join(
            f"### {title_prefix} {index}\n\n{content}"
            for index, content in enumerate(evaluation_material[key], start=1)
        )

    return INPUT_TEMPLATES["05_ab_test_evaluation"].format(
        improvement=evaluation_material["improvement"],
        improvement_goals=bullets("improvement_goals"),
        prohibited_patterns=bullets("prohibited_patterns"),
        original_tests=tests("original_tests", "원본 테스트 대화"),
        improved_tests=tests("improved_tests", "개선 테스트 대화"),
    ).strip()


def parse_evaluation_report(result_text: str):
    lines = result_text.splitlines()
    metrics = []

    try:
        table_start = next(
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("| 평가 지표 |")
        )
    except StopIteration:
        return None

    for line in lines[table_start + 2 :]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break

        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) != 11 or set(cells[0]) == {"-"}:
            continue

        metrics.append(
            {
                "metric": cells[0],
                "criterion": cells[1],
                "original_1": cells[2],
                "original_2": cells[3],
                "original_3": cells[4],
                "original_summary": cells[5],
                "improved_1": cells[6],
                "improved_2": cells[7],
                "improved_3": cells[8],
                "improved_summary": cells[9],
                "change": cells[10],
            }
        )

    def section(start_heading, end_heading=None):
        pattern = rf"^{re.escape(start_heading)}\s*$\n(.*)"
        if end_heading:
            pattern = (
                rf"^{re.escape(start_heading)}\s*$\n(.*?)"
                rf"(?=^{re.escape(end_heading)}\s*$)"
            )
        match = re.search(pattern, result_text, flags=re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else ""

    def bullet_items(section_text):
        return [
            match.group(1).strip()
            for line in section_text.splitlines()
            if (match := re.match(r"^\s*[-*]\s+(.+)$", line))
        ]

    def labeled_value(section_text, label):
        match = re.search(
            rf"^\s*[-*]\s*{re.escape(label)}\s*:\s*(.+)$",
            section_text,
            flags=re.MULTILINE,
        )
        return match.group(1).strip() if match else ""

    comparison_summary = bullet_items(
        section("### 비교 요약", "## 개선 성공 여부")
    )
    success_section = section("## 개선 성공 여부", "## 잔존 문제")
    residual_section = section("## 잔존 문제", "## 재수정 필요 여부")
    revision_section = section("## 재수정 필요 여부")

    report = {
        "metrics": metrics,
        "comparison_summary": comparison_summary,
        "success": {
            "verdict": labeled_value(success_section, "판정"),
            "rationale": labeled_value(success_section, "판단 근거"),
        },
        "residual_problems": bullet_items(residual_section),
        "revision": {
            "verdict": labeled_value(revision_section, "판정"),
            "rationale": labeled_value(revision_section, "판단 근거"),
        },
    }

    if (
        not report["metrics"]
        or not report["comparison_summary"]
        or not report["success"]["verdict"]
        or not report["success"]["rationale"]
        or not report["residual_problems"]
        or not report["revision"]["verdict"]
        or not report["revision"]["rationale"]
    ):
        return None

    return report


@app.get("/")
@app.get("/index.html")
@app.get("/test.html")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/static/<path:filename>")
def static_assets(filename):
    return send_from_directory(BASE_DIR / "static", filename)


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "api_key_configured": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        }
    )


@app.post("/api/analyze")
def analyze():
    if not request.is_json:
        return error_response("JSON 형식으로 요청해 주세요.", 415)

    payload = request.get_json(silent=True)
    user_material, validation_error = validate_payload(payload)

    if validation_error:
        return error_response(validation_error, 400)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return error_response(
            "서버에 OPENAI_API_KEY가 설정되지 않았어요. API 키를 설정한 뒤 서버를 다시 실행해 주세요.",
            503,
        )

    model = os.getenv("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
    diagnosis_input = INPUT_TEMPLATES["01_problem_diagnosis"].format(
        improvement=user_material["improvement"],
        og_prompt=user_material["og_prompt"],
        og_intro=user_material["og_intro"],
        og_chatHistory=user_material["og_chatHistory"],
    ).strip()

    try:
        client = OpenAI(api_key=api_key, timeout=180.0)

        diagnosis_response = create_openai_response(
            client,
            model=model,
            reasoning={"effort": "medium"},
            instructions=instructions_for(
                DIAGNOSIS_INSTRUCTIONS,
                user_material["output_language"],
            ),
            input=diagnosis_input,
        )

        diagnosis_raw_response = (diagnosis_response.output_text or "").strip()
        if not diagnosis_raw_response:
            app.logger.error("The first OpenAI response did not contain output text.")
            return error_response(
                "GPT의 문제 진단 응답이 비어 있어요. 잠시 후 다시 시도해 주세요.",
                502,
            )

        diagnosis = parse_diagnosis(diagnosis_raw_response)
        if not has_complete_diagnosis(diagnosis):
            app.logger.error(
                "Could not parse all diagnosis sections. Parsed keys: %s",
                [key for key, value in diagnosis.items() if value],
            )
            return error_response(
                "GPT 문제 진단 결과의 형식을 읽을 수 없어요. 다시 시도해 주세요.",
                502,
            )

        improvement_input = format_diagnosis_input(diagnosis, user_material)
        improvement_response = create_openai_response(
            client,
            model=model,
            reasoning={"effort": "medium"},
            instructions=instructions_for(
                IMPROVEMENT_INSTRUCTIONS,
                user_material["output_language"],
            ),
            input=improvement_input,
        )

        result = (improvement_response.output_text or "").strip()
        if not result:
            app.logger.error("The second OpenAI response did not contain output text.")
            return error_response(
                "GPT의 프롬프트 개선 응답이 비어 있어요. 잠시 후 다시 시도해 주세요.",
                502,
            )

        improved_prompt, improved_intro = parse_improvement_result(result)
        if not improved_prompt or not improved_intro:
            app.logger.error("Could not parse the improved prompt and intro sections.")
            return error_response(
                "GPT 개선 결과의 형식을 읽을 수 없어요. 다시 시도해 주세요.",
                502,
            )

        return jsonify(
            {
                "result": result,
                "output_language": user_material["output_language"],
                "diagnosis": diagnosis,
                "diagnosis_raw_response": diagnosis_raw_response,
                "improved_prompt": improved_prompt,
                "improved_intro": improved_intro,
            }
        )

    except OpenAIDailyCallLimitExceeded:
        return error_response("하루 이용 횟수를 초과하였습니다.", 429)
    except AuthenticationError:
        app.logger.exception("OpenAI authentication failed.")
        return error_response(
            "OpenAI API 키를 인증할 수 없어요. 서버의 API 키를 확인해 주세요.",
            401,
        )
    except RateLimitError:
        app.logger.exception("OpenAI rate limit reached.")
        return error_response(
            "현재 요청이 많거나 API 사용 한도에 도달했어요. 잠시 후 다시 시도해 주세요.",
            429,
        )
    except APIConnectionError:
        app.logger.exception("Could not connect to OpenAI.")
        return error_response(
            "OpenAI 서버에 연결할 수 없어요. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.",
            503,
        )
    except APIStatusError:
        app.logger.exception("OpenAI returned an API status error.")
        return error_response(
            "OpenAI에서 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except OpenAIError:
        app.logger.exception("OpenAI request failed.")
        return error_response(
            "GPT 진단 또는 개선 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except Exception:
        app.logger.exception("Unexpected diagnosis or improvement error.")
        return error_response(
            "서버에서 예상하지 못한 오류가 발생했어요.",
            500,
        )


@app.post("/api/compile")
def compile_prompt():
    if not request.is_json:
        return error_response("JSON 형식으로 요청해 주세요.", 415)

    payload = request.get_json(silent=True)
    compile_material, validation_error = validate_compile_payload(payload)

    if validation_error:
        return error_response(validation_error, 400)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return error_response(
            "서버에 OPENAI_API_KEY가 설정되지 않았어요. API 키를 설정한 뒤 서버를 다시 실행해 주세요.",
            503,
        )

    model = os.getenv("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
    compilation_input = format_compilation_input(compile_material)

    try:
        client = OpenAI(api_key=api_key, timeout=180.0)
        compilation_response = create_openai_response(
            client,
            model=model,
            reasoning={"effort": "medium"},
            instructions=instructions_for(
                COMPILATION_INSTRUCTIONS,
                compile_material["output_language"],
            ),
            input=compilation_input,
        )

        result = (compilation_response.output_text or "").strip()
        if not result:
            app.logger.error("The third OpenAI response did not contain output text.")
            return error_response(
                "GPT의 최종 프롬프트 응답이 비어 있어요. 잠시 후 다시 시도해 주세요.",
                502,
            )

        final_prompt, final_intro = parse_compilation_result(result)
        if not final_prompt or not final_intro:
            app.logger.error("Could not parse the final prompt and intro sections.")
            return error_response(
                "GPT 최종 결과의 형식을 읽을 수 없어요. 다시 시도해 주세요.",
                502,
            )

        return jsonify(
            {
                "result": result,
                "output_language": compile_material["output_language"],
                "final_prompt": final_prompt,
                "final_intro": final_intro,
            }
        )

    except OpenAIDailyCallLimitExceeded:
        return error_response("하루 이용 횟수를 초과하였습니다.", 429)
    except AuthenticationError:
        app.logger.exception("OpenAI authentication failed during compilation.")
        return error_response(
            "OpenAI API 키를 인증할 수 없어요. 서버의 API 키를 확인해 주세요.",
            401,
        )
    except RateLimitError:
        app.logger.exception("OpenAI rate limit reached during compilation.")
        return error_response(
            "현재 요청이 많거나 API 사용 한도에 도달했어요. 잠시 후 다시 시도해 주세요.",
            429,
        )
    except APIConnectionError:
        app.logger.exception("Could not connect to OpenAI during compilation.")
        return error_response(
            "OpenAI 서버에 연결할 수 없어요. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.",
            503,
        )
    except APIStatusError:
        app.logger.exception("OpenAI returned an API status error during compilation.")
        return error_response(
            "OpenAI에서 최종 프롬프트 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except OpenAIError:
        app.logger.exception("OpenAI compilation request failed.")
        return error_response(
            "GPT 최종 프롬프트 생성 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except Exception:
        app.logger.exception("Unexpected compilation error.")
        return error_response(
            "서버에서 예상하지 못한 오류가 발생했어요.",
            500,
        )


@app.post("/api/generate-tests")
def generate_tests():
    if not request.is_json:
        return error_response("JSON 형식으로 요청해 주세요.", 415)

    payload = request.get_json(silent=True)
    test_material, validation_error = validate_test_payload(payload)

    if validation_error:
        return error_response(validation_error, 400)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return error_response(
            "서버에 OPENAI_API_KEY가 설정되지 않았어요. API 키를 설정한 뒤 서버를 다시 실행해 주세요.",
            503,
        )

    model = os.getenv("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
    test_input = format_test_generation_input(test_material)

    try:
        client = OpenAI(api_key=api_key, timeout=300.0)
        test_response = create_openai_response(
            client,
            model=model,
            reasoning={"effort": "medium"},
            instructions=instructions_for(
                TEST_GENERATION_INSTRUCTIONS,
                test_material["output_language"],
            ),
            input=test_input,
        )

        result = (test_response.output_text or "").strip()
        if not result:
            app.logger.error("The test-generation response did not contain output text.")
            return error_response(
                "GPT의 테스트 대화 응답이 비어 있어요. 잠시 후 다시 시도해 주세요.",
                502,
            )

        if not has_valid_test_result(result):
            app.logger.error("The test-generation response did not match the output format.")
            return error_response(
                "GPT 테스트 대화 결과의 형식을 읽을 수 없어요. 다시 시도해 주세요.",
                502,
            )

        parsed_tests = parse_generated_tests(result)
        if not parsed_tests:
            app.logger.error("Could not parse all six generated test conversations.")
            return error_response(
                "GPT 테스트 대화 6개를 구분할 수 없어요. 다시 시도해 주세요.",
                502,
            )

        return jsonify(
            {
                "result": result,
                "output_language": test_material["output_language"],
                "original_tests": parsed_tests["original_tests"],
                "improved_tests": parsed_tests["improved_tests"],
            }
        )

    except OpenAIDailyCallLimitExceeded:
        return error_response("하루 이용 횟수를 초과하였습니다.", 429)
    except AuthenticationError:
        app.logger.exception("OpenAI authentication failed during test generation.")
        return error_response(
            "OpenAI API 키를 인증할 수 없어요. 서버의 API 키를 확인해 주세요.",
            401,
        )
    except RateLimitError:
        app.logger.exception("OpenAI rate limit reached during test generation.")
        return error_response(
            "현재 요청이 많거나 API 사용 한도에 도달했어요. 잠시 후 다시 시도해 주세요.",
            429,
        )
    except APIConnectionError:
        app.logger.exception("Could not connect to OpenAI during test generation.")
        return error_response(
            "OpenAI 서버에 연결할 수 없어요. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.",
            503,
        )
    except APIStatusError:
        app.logger.exception(
            "OpenAI returned an API status error during test generation."
        )
        return error_response(
            "OpenAI에서 테스트 대화 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except OpenAIError:
        app.logger.exception("OpenAI test-generation request failed.")
        return error_response(
            "GPT 테스트 대화 생성 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except Exception:
        app.logger.exception("Unexpected test-generation error.")
        return error_response(
            "서버에서 예상하지 못한 오류가 발생했어요.",
            500,
        )


@app.post("/api/evaluate-tests")
def evaluate_tests():
    if not request.is_json:
        return error_response("JSON 형식으로 요청해 주세요.", 415)

    payload = request.get_json(silent=True)
    evaluation_material, validation_error = validate_evaluation_payload(payload)

    if validation_error:
        return error_response(validation_error, 400)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return error_response(
            "서버에 OPENAI_API_KEY가 설정되지 않았어요. API 키를 설정한 뒤 서버를 다시 실행해 주세요.",
            503,
        )

    model = os.getenv("OPENAI_MODEL", "gpt-5.6-sol").strip() or "gpt-5.6-sol"
    evaluation_input = format_evaluation_input(evaluation_material)

    try:
        client = OpenAI(api_key=api_key, timeout=300.0)
        evaluation_response = create_openai_response(
            client,
            model=model,
            reasoning={"effort": "medium"},
            instructions=instructions_for(
                EVALUATION_INSTRUCTIONS,
                evaluation_material["output_language"],
            ),
            input=evaluation_input,
        )

        result = (evaluation_response.output_text or "").strip()
        if not result:
            app.logger.error("The evaluation response did not contain output text.")
            return error_response(
                "GPT의 A/B 테스트 평가 응답이 비어 있어요. 잠시 후 다시 시도해 주세요.",
                502,
            )

        report = parse_evaluation_report(result)
        if not report:
            app.logger.error("Could not parse the A/B test evaluation report.")
            return error_response(
                "GPT A/B 테스트 평가 결과의 형식을 읽을 수 없어요. 다시 시도해 주세요.",
                502,
            )

        return jsonify(
            {
                "result": result,
                "output_language": evaluation_material["output_language"],
                "report": report,
            }
        )

    except OpenAIDailyCallLimitExceeded:
        return error_response("하루 이용 횟수를 초과하였습니다.", 429)
    except AuthenticationError:
        app.logger.exception("OpenAI authentication failed during evaluation.")
        return error_response(
            "OpenAI API 키를 인증할 수 없어요. 서버의 API 키를 확인해 주세요.",
            401,
        )
    except RateLimitError:
        app.logger.exception("OpenAI rate limit reached during evaluation.")
        return error_response(
            "현재 요청이 많거나 API 사용 한도에 도달했어요. 잠시 후 다시 시도해 주세요.",
            429,
        )
    except APIConnectionError:
        app.logger.exception("Could not connect to OpenAI during evaluation.")
        return error_response(
            "OpenAI 서버에 연결할 수 없어요. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.",
            503,
        )
    except APIStatusError:
        app.logger.exception("OpenAI returned an API status error during evaluation.")
        return error_response(
            "OpenAI에서 A/B 테스트 평가 요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except OpenAIError:
        app.logger.exception("OpenAI evaluation request failed.")
        return error_response(
            "GPT A/B 테스트 평가 중 오류가 발생했어요. 잠시 후 다시 시도해 주세요.",
            502,
        )
    except Exception:
        app.logger.exception("Unexpected evaluation error.")
        return error_response(
            "서버에서 예상하지 못한 오류가 발생했어요.",
            500,
        )


@app.errorhandler(413)
def request_too_large(_error):
    return error_response(
        "전송한 내용이 너무 커요. 입력 내용을 줄인 뒤 다시 시도해 주세요.",
        413,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="127.0.0.1", port=port, debug=False)
