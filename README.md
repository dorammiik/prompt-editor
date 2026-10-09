# Character-chat Prompt Editor

**Language: [한국어](https://github.com/dorammiik/prompt-editor/blob/main/README%28ko%29.md) | English**

📄 [기획안](https://app.notion.com/p/Character-chat-Prompt-Editor-3a9856fc07af804e8f22f3028575fcdf?source=copy_link) | 📄 [Specification (EN)](https://app.notion.com/p/Product-Specification-Character-Chat-Prompt-Editor-a79856fc07af822ca08c8191278489a4?source=copy_link) | 📚 [Dataset (KO)](https://docs.google.com/spreadsheets/d/17gqW3UED_Fl9mr9UPCzbobPYpJAL6mkP3xsH6ADfFuo/edit?usp=sharing) | 📚 [Dataset (EN)](https://docs.google.com/spreadsheets/d/1tIvcCV1qE68o7VNaVK0bi4ROC-P8qqy-WTiaqtXHI9o/edit?usp=sharing)


An AI-powered prompt improvement tool for character-chat creators.

It analyzes issues found in actual character conversations, identifies the prompt patterns causing them, generates an improved prompt, and validates the changes through before/after conversation simulations.

🔗 **[Try the Prototype](http://3.36.154.8/?lang=ko)**

---

## Overview

Character-chat creators often encounter cases where a character behaves differently from their intended personality, relationship, or world setting — but identifying which part of a long prompt caused the issue can be difficult.

Character-chat Prompt Editor turns this process into a structured workflow:

1. **Diagnose** the issue from the creator's prompt and conversation data
2. **Improve** the prompt while preserving the original character settings
3. **Compile** the improved prompt to fit platform-specific fields and length limits
4. **Simulate** conversations using both the original and improved prompts
5. **Evaluate** the results with the same criteria through A/B testing

```text
Issue & Conversation
        ↓
Problem Diagnosis
        ↓
Prompt Improvement
        ↓
Prompt Compilation
        ↓
Before / After Simulation
        ↓
A/B Evaluation
```

## Evaluation
The MVP was evaluated using 18 character-chat issue cases collected from real character-chat scenarios across three platforms.
- 18 test cases
- 8 character-chat stories
- 108 simulated conversations
  - 54 with original prompts
  - 54 with improved prompts
- Quality areas:
  - Character behavior & consistency
  - Relationship dynamics
  - Worldbuilding & setting
  - Narrative style & pacing
All 18 cases passed the internal A/B evaluation criteria in the initial validation.
The current evaluation focuses on single-issue cases. Multi-issue requests and failure/partial-success cases require further validation.


## Demo

The prototype demonstrates the full workflow from issue diagnosis to prompt improvement, conversation simulation, and A/B evaluation.
