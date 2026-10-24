import asyncio
import json

from .errors import ProcessingError
from .schemas import Choice, Question, QuizResult, integer, require, text

PROMPT_VERSION = "unit-quiz-course-v3"


def quiz_schema():
    string = {"type": "string"}
    choice = {
        "type": "object",
        "properties": {
            "choice_no": {"type": "integer", "minimum": 1, "maximum": 4},
            "content": string,
        },
        "required": ["choice_no", "content"],
        "additionalProperties": False,
    }
    question = {
        "type": "object",
        "properties": {
            "order_no": {"type": "integer", "minimum": 1},
            "content": string,
            "choices": {"type": "array", "items": choice, "minItems": 4, "maxItems": 4},
            "correct_choice_no": {"type": "integer", "minimum": 1, "maximum": 4},
            "explanation": string,
        },
        "required": ["order_no", "content", "choices", "correct_choice_no", "explanation"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"questions": {"type": "array", "items": question, "maxItems": 5}},
        "required": ["questions"],
        "additionalProperties": False,
    }


def parse_questions(raw, count):
    try:
        require(isinstance(raw, dict) and set(raw) == {"questions"}, "INVALID_QUIZ")
        items = raw["questions"]
        require(isinstance(items, list), "INVALID_QUIZ")
        require(bool(items), "INSUFFICIENT_CONTENT")
        require(len(items) == count, "INVALID_QUESTION_COUNT")
        questions = []
        seen = set()
        for index, q in enumerate(items, 1):
            require(
                set(q) == {"order_no", "content", "choices", "correct_choice_no", "explanation"},
                "INVALID_QUIZ",
            )
            require(integer(q["order_no"]) and q["order_no"] == index, "INVALID_QUESTION_ORDER")
            require(text(q["content"]) and text(q["explanation"]), "EMPTY_QUESTION")
            normalized = " ".join(q["content"].split()).casefold()
            require(normalized not in seen, "DUPLICATE_QUESTION")
            seen.add(normalized)
            require(
                integer(q["correct_choice_no"]) and q["correct_choice_no"] <= 4, "INVALID_ANSWER"
            )
            choices = q["choices"]
            require(isinstance(choices, list) and len(choices) == 4, "INVALID_CHOICES")
            for c in choices:
                require(set(c) == {"choice_no", "content"}, "INVALID_CHOICES")
                require(
                    integer(c["choice_no"])
                    and text(c["content"])
                    and len(c["content"].strip()) <= 500,
                    "INVALID_CHOICES",
                )
            require(sorted(c["choice_no"] for c in choices) == [1, 2, 3, 4], "INVALID_CHOICES")
            require(
                len({" ".join(c["content"].split()).casefold() for c in choices}) == 4,
                "DUPLICATE_CHOICES",
            )
            questions.append(
                Question(
                    index,
                    q["content"].strip(),
                    tuple(
                        Choice(c["choice_no"], c["content"].strip())
                        for c in sorted(choices, key=lambda c: c["choice_no"])
                    ),
                    q["correct_choice_no"],
                    q["explanation"].strip(),
                )
            )
        return tuple(questions)
    except (TypeError, KeyError, AttributeError):
        raise ProcessingError("INVALID_QUIZ") from None


class QuizService:
    def __init__(self, provider, max_input_chars=120000):
        self.provider, self.max_input_chars = provider, max_input_chars

    def generate(self, request):
        payload = request.payload()
        if len(json.dumps(payload, ensure_ascii=False)) > self.max_input_chars:
            raise ProcessingError("SECTION_INPUT_TOO_LARGE")
        raw = self.provider.generate(payload, quiz_schema())
        questions = parse_questions(raw, request.question_count)
        return QuizResult(
            request.course_id,
            request.section_id,
            request.fingerprint(payload),
            questions,
            model=getattr(self.provider, "model", ""),
            prompt_version=PROMPT_VERSION,
        )

    async def generate_async(self, request):
        return await asyncio.to_thread(self.generate, request)
