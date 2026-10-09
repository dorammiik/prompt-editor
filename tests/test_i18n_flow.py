import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app as app_module


def response(text):
    return SimpleNamespace(output_text=text)


def diagnosis(language):
    values = {
        "ko": {
            "target": "내레이션",
            "symptom": "응답마다 내레이션이 여섯 문장 이상 이어짐",
            "goal": "각 응답의 내레이션은 최대 3문장",
            "prohibited": "동일한 감정을 반복해서 설명",
            "cause": "내레이션 분량 제한이 없음",
        },
        "en": {
            "target": "Narration",
            "symptom": "Each response contains at least six narration sentences",
            "goal": "Use no more than three narration sentences per response",
            "prohibited": "Do not repeat the same emotion in different wording",
            "cause": "The prompt does not limit narration length",
        },
    }[language]
    return f"""# 문제 진단 결과

## 개선 대상

- {values["target"]}

## 문제 현상

- {values["symptom"]}

## 개선 목표

- {values["goal"]}

## 금지 사항

- {values["prohibited"]}

## 원본 프롬프트 문제 원인

- {values["cause"]}
"""


def improvement(language):
    if language == "en":
        prompt = "Write concise character dialogue with no more than three narration sentences."
        intro = "Kang Dokhyeon: Why are you late?"
    else:
        prompt = "내레이션은 최대 세 문장으로 쓰고 캐릭터 대사를 간결하게 작성한다."
        intro = "강독현: 왜 늦었어?"
    return f"""# 개선 프롬프트

{prompt}

# 개선 인트로 대화

{intro}
"""


def compilation(language):
    if language == "en":
        prompt = "# Output Rules\n\nUse no more than three narration sentences."
        intro = "Kang Dokhyeon: Why are you late?"
    else:
        prompt = "# 출력 규칙\n\n내레이션은 최대 세 문장으로 작성한다."
        intro = "강독현: 왜 늦었어?"
    return f"""# 최종 개선 프롬프트

{prompt}

# 최종 개선 인트로 대화

{intro}
"""


def generated_tests(language):
    original = (
        "Jia looked down. She tightened her grip. The room felt cold."
        if language == "en"
        else "지아는 고개를 숙였다. 손에 힘이 들어갔다. 방 안은 차가웠다."
    )
    improved = (
        "Jia looked down.\n\nKang Dokhyeon: Answer me."
        if language == "en"
        else "지아는 고개를 숙였다.\n\n강독현: 대답해."
    )
    return f"""# 테스트 응답 생성 결과

## 원본 프롬프트 테스트

### 원본 테스트 대화 1

{original}

=====

### 원본 테스트 대화 2

{original}

=====

### 원본 테스트 대화 3

{original}

---

## 개선 프롬프트 테스트

### 개선 테스트 대화 1

{improved}

=====

### 개선 테스트 대화 2

{improved}

=====

### 개선 테스트 대화 3

{improved}
"""


def evaluation(language):
    if language == "en":
        metric = "Narration sentences per response"
        criterion = "Maximum 3 sentences"
        passed = "3 sentences (pass)"
        improved = "1 sentence (pass)"
        summary = "All three dialogues pass"
        change = "Average decreased by 2.0 sentences"
        comparison = "All improved dialogues remain within the narration limit."
        verdict = "Success"
        rationale = "All three improved dialogues meet the absolute criterion."
        residual = "No remaining issues identified."
        revision = "Not needed"
        revision_reason = "No target or prohibited-pattern violations remain."
    else:
        metric = "응답당 내레이션 문장 수"
        criterion = "최대 3문장"
        passed = "3문장(통과)"
        improved = "1문장(통과)"
        summary = "3개 대화 모두 통과"
        change = "평균 2.0문장 감소"
        comparison = "개선 대화 3개 모두 내레이션 제한을 충족함."
        verdict = "성공"
        rationale = "개선 대화 3개가 절대 기준을 모두 충족함."
        residual = "확인된 잔존 문제 없음"
        revision = "불필요"
        revision_reason = "개선 목표와 금지 사항 위반이 남아 있지 않음."

    return f"""# A/B 테스트 결과 평가

## 원본과 개선본의 지표 비교

| 평가 지표 | 판정 기준 | 원본 테스트 대화 1 | 원본 테스트 대화 2 | 원본 테스트 대화 3 | 원본 종합 | 개선 테스트 대화 1 | 개선 테스트 대화 2 | 개선 테스트 대화 3 | 개선본 종합 | 변화 및 비교 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| {metric} | {criterion} | {passed} | {passed} | {passed} | {summary} | {improved} | {improved} | {improved} | {summary} | {change} |

### 비교 요약

- {comparison}

## 개선 성공 여부

- 판정: {verdict}
- 판단 근거: {rationale}

## 잔존 문제

- {residual}

## 재수정 필요 여부

- 판정: {revision}
- 판단 근거: {revision_reason}
"""


class I18nFlowTest(unittest.TestCase):
    def setUp(self):
        self.original_api_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"
        self.client = app_module.app.test_client()

    def tearDown(self):
        if self.original_api_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.original_api_key

    def run_flow(self, language):
        mocked_outputs = [
            response(diagnosis(language)),
            response(improvement(language)),
            response(compilation(language)),
            response(generated_tests(language)),
            response(evaluation(language)),
        ]
        captured_calls = []

        def fake_openai_response(_client, **kwargs):
            captured_calls.append(kwargs)
            return mocked_outputs[len(captured_calls) - 1]

        base_payload = {
            "improvement": (
                "The narration is too long."
                if language == "en"
                else "내레이션이 너무 길어요."
            ),
            "og_prompt": "Original prompt",
            "og_intro": "Original intro",
            "og_chatHistory": "Original response",
            "output_language": language,
        }

        with patch.object(
            app_module,
            "create_openai_response",
            side_effect=fake_openai_response,
        ):
            analyze_response = self.client.post("/api/analyze", json=base_payload)
            self.assertEqual(analyze_response.status_code, 200)
            analyze_data = analyze_response.get_json()
            self.assertEqual(analyze_data["output_language"], language)

            compile_response = self.client.post(
                "/api/compile",
                json={
                    "improvement": base_payload["improvement"],
                    "diagnosis": analyze_data["diagnosis"],
                    "improved_prompt": analyze_data["improved_prompt"],
                    "improved_intro": analyze_data["improved_intro"],
                    "required_prompts": "Prompt / Introduction",
                    "output_language": language,
                },
            )
            self.assertEqual(compile_response.status_code, 200)
            compile_data = compile_response.get_json()

            test_response = self.client.post(
                "/api/generate-tests",
                json={
                    "improvement": base_payload["improvement"],
                    "og_prompt": base_payload["og_prompt"],
                    "og_intro": base_payload["og_intro"],
                    "final_prompt": compile_data["final_prompt"],
                    "final_intro": compile_data["final_intro"],
                    "test_chat_context": "",
                    "test_user_input": "Lee Jia: I missed the bus.",
                    "output_language": language,
                },
            )
            self.assertEqual(test_response.status_code, 200)
            test_data = test_response.get_json()
            self.assertEqual(len(test_data["original_tests"]), 3)
            self.assertEqual(len(test_data["improved_tests"]), 3)

            evaluation_response = self.client.post(
                "/api/evaluate-tests",
                json={
                    "improvement": base_payload["improvement"],
                    "improvement_goals": analyze_data["diagnosis"][
                        "improvement_goals"
                    ],
                    "prohibited_patterns": analyze_data["diagnosis"][
                        "prohibited_patterns"
                    ],
                    "original_tests": test_data["original_tests"],
                    "improved_tests": test_data["improved_tests"],
                    "output_language": language,
                },
            )
            self.assertEqual(evaluation_response.status_code, 200)
            evaluation_data = evaluation_response.get_json()
            self.assertEqual(evaluation_data["output_language"], language)
            self.assertTrue(evaluation_data["report"]["metrics"])

        self.assertEqual(len(captured_calls), 5)
        for call in captured_calls:
            self.assertIn("instructions", call)
            self.assertIn("input", call)
            if language == "en":
                self.assertIn(
                    "Write all user-visible result content in English",
                    call["instructions"],
                )
            else:
                self.assertIn(
                    "실제 결과 내용은 한국어로 작성한다",
                    call["instructions"],
                )

        if language == "en":
            self.assertIn("Write concise", analyze_data["improved_prompt"])
            self.assertEqual(
                evaluation_data["report"]["success"]["verdict"],
                "Success",
            )
        else:
            self.assertIn("내레이션", analyze_data["improved_prompt"])
            self.assertEqual(
                evaluation_data["report"]["success"]["verdict"],
                "성공",
            )

    def test_korean_full_flow(self):
        self.run_flow("ko")

    def test_english_full_flow(self):
        self.run_flow("en")

    def test_error_codes(self):
        response = self.client.post(
            "/api/analyze",
            json={"output_language": "fr"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "INVALID_OUTPUT_LANGUAGE")

        response = self.client.post(
            "/api/analyze",
            data="not-json",
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["code"], "INVALID_JSON")


if __name__ == "__main__":
    unittest.main()
