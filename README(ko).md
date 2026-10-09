# Character-chat Prompt Editor

Language: **한국어** | [English](https://github.com/dorammiik/prompt-editor/tree/main#character-chat-prompt-editor)

📄 [기획안](https://app.notion.com/p/Character-chat-Prompt-Editor-3a9856fc07af804e8f22f3028575fcdf?source=copy_link) | 📄 [Specification (EN)](https://app.notion.com/p/Product-Specification-Character-Chat-Prompt-Editor-a79856fc07af822ca08c8191278489a4?source=copy_link) | 📚 [Dataset (KO)](https://docs.google.com/spreadsheets/d/17gqW3UED_Fl9mr9UPCzbobPYpJAL6mkP3xsH6ADfFuo/edit?usp=sharing) | 📚 [Dataset (EN)](https://docs.google.com/spreadsheets/d/1tIvcCV1qE68o7VNaVK0bi4ROC-P8qqy-WTiaqtXHI9o/edit?usp=sharing)

캐릭터챗 대화에서 발생한 문제를 분석하고, 문제를 개선한 프롬프트를 제안하는 AI 기반 Prompt Editor입니다. 

창작자가 감으로 프롬프트를 반복 수정하는 과정을 줄이고, 실제 대화 데이터를 기반으로 캐릭터챗의 품질을 관리할 수 있도록 하는 것을 목표로 합니다.

🔗 **[프로토타입 체험하기](http://3.36.154.8/?lang=ko)**

---

## Overview

캐릭터챗 창작자는 캐릭터가 자신의 설정이나 의도와 다르게 행동하는 문제를 자주 경험하지만, 긴 프롬프트 안에서 어떤 부분을 수정해야 하는지 파악하기 어렵습니다.

Character-chat Prompt Editor는 창작자가 입력한 **개선 요청 사항, 원본 프롬프트, 원본 인트로 대화, 원본 대화**를 바탕으로 다음 과정을 수행합니다.

1. **문제 진단**  
   개선 요청을 측정 가능한 문제 단위로 구조화하고, 원본 대화에서 **문제 현상**을 확인한 뒤 **개선 목표, 금지 사항, 원본 프롬프트 문제 원인**을 진단합니다.

2. **프롬프트 개선**  
   문제 진단 결과를 바탕으로 기존 설정을 유지하면서 **개선 프롬프트와 개선 인트로 대화**를 생성합니다.

3. **최종 프롬프트 컴파일**  
   개선 결과를 사용자가 이용하는 캐릭터챗 플랫폼의 **프롬프트 항목과 글자 수 제한**에 맞게 분리·압축·재구성합니다.

4. **테스트 대화 생성**  
   원본 프롬프트와 최종 개선 프롬프트를 동일한 테스트 조건에서 실행하여 **원본 테스트 대화와 개선 테스트 대화**를 생성합니다.

5. **A/B 테스트 평가**  
   생성된 대화를 동일한 평가 기준으로 측정하여 프롬프트 개선 효과를 평가하고 **개선 성공 여부, 잔존 문제, 재수정 필요 여부**를 제공합니다.

```text
개선 요청 사항 & 실제 대화
        ↓
문제 진단
        ↓
프롬프트 개선
        ↓
최종 프롬프트 컴파일
        ↓
원본 / 개선 테스트 대화 생성
        ↓
A/B 테스트 평가
        ↓
결과 리포트
```

## Evaluation
MVP는 실제 캐릭터챗 문제 사례를 바탕으로 구성한 18개의 단일 문제 테스트 케이스를 통해 검증했습니다.
- 18개 테스트 케이스
- 8개 캐릭터챗 스토리
- 3개 대상 플랫폼
- 108개 시뮬레이션 대화
  - 원본 테스트 대화 54개
  - 개선 테스트 대화 54개
- 품질 영역
  - 성격
  - 관계
  - 세계관·설정
  - 서술·전개

각 케이스마다 원본과 개선 테스트 대화를 각각 3개씩 생성하고, 개선 요청 사항과 개선 목표를 기준으로 동일한 평가 지표를 적용했습니다.

초기 내부 검증에서 18/18건이 A/B 평가 기준을 통과했으며, 개선 테스트 3개 모두 통과했습니다.

- 현재 검증은 한 번에 하나의 문제를 다루는 단일 문제 사례를 중심으로 진행되었습니다.
- 복합 문제와 부분 성공·실패·판정 유보 사례에 대해서는 추가 검증이 필요합니다.

## Demo
프로토타입에서는 개선 요청 및 데이터 입력 → 문제 진단 → 프롬프트 개선 → 최종 프롬프트 컴파일 → 테스트 대화 생성 → A/B 테스트 평가 → 결과 리포트까지 전체 흐름을 확인할 수 있습니다.
