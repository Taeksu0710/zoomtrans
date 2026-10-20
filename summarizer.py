import httpx
import ollama

CHUNK_SYSTEM_PROMPT = (
    "당신은 회의록 정리 전문가입니다. 아래는 Zoom 회의 중 자동으로 기록된 "
    "영어 원문/한국어 번역 발화 기록의 일부입니다. 이 구간에서 다뤄진 내용을 "
    "한국어로 간결하게 요약하세요. 시간순 흐름을 유지하고, 사실만 정리하세요."
)

FINAL_SYSTEM_PROMPT = (
    "당신은 회의록 정리 전문가입니다. 아래는 긴 회의를 구간별로 나눠 요약한 "
    "내용입니다. 이를 종합해 하나의 회의록으로 정리하세요. 다음 형식을 따르세요:\n\n"
    "## 회의 개요\n"
    "(회의 전체의 흐름, 맥락, 목적, 전반적인 결과를 3~5문장의 서술형 문단으로 요약)\n\n"
    "## 주요 논의 내용\n"
    "- ...\n\n"
    "## 결정 사항\n"
    "- ...\n\n"
    "## 액션 아이템\n"
    "- ...\n\n"
    "해당 없는 항목은 \"없음\"이라고 표시하세요."
)

SINGLE_PASS_SYSTEM_PROMPT = (
    "당신은 회의록 정리 전문가입니다. 아래는 Zoom 회의 중 자동으로 기록된 "
    "영어 원문/한국어 번역 발화 기록입니다. 이를 바탕으로 다음 형식의 "
    "한국어 회의록을 작성하세요:\n\n"
    "## 회의 개요\n"
    "(회의 전체의 흐름, 맥락, 목적, 전반적인 결과를 3~5문장의 서술형 문단으로 요약)\n\n"
    "## 주요 논의 내용\n"
    "- ...\n\n"
    "## 결정 사항\n"
    "- ...\n\n"
    "## 액션 아이템\n"
    "- ...\n\n"
    "해당 없는 항목은 \"없음\"이라고 표시하세요."
)

CHUNK_CHAR_LIMIT = 6000
SUMMARY_TIMEOUT  = 120  # 초


class MeetingSummarizer:
    def __init__(self, model: str):
        self.model   = model
        self._client = ollama.Client(timeout=httpx.Timeout(SUMMARY_TIMEOUT))

    def _chat(self, system_prompt: str, user_content: str) -> str:
        response = self._client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            stream=False,
        )
        return response["message"]["content"].strip()

    def _split_into_chunks(self, text: str) -> list[str]:
        entries = text.split("\n\n")
        chunks = []
        current = ""
        for entry in entries:
            candidate = f"{current}\n\n{entry}" if current else entry
            if len(candidate) > CHUNK_CHAR_LIMIT and current:
                chunks.append(current)
                current = entry
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def summarize_file(self, filepath: str) -> str:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        if len(text) <= CHUNK_CHAR_LIMIT:
            return self._chat(SINGLE_PASS_SYSTEM_PROMPT, text)

        chunks = self._split_into_chunks(text)
        partials = [self._chat(CHUNK_SYSTEM_PROMPT, chunk) for chunk in chunks]
        combined = "\n\n".join(
            f"[구간 {i+1}]\n{p}" for i, p in enumerate(partials)
        )
        return self._chat(FINAL_SYSTEM_PROMPT, combined)
