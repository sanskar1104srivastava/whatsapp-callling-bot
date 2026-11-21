import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("survey_manager")

class SurveyManager:
    def __init__(self, kb_path: str):
        self.kb_path = kb_path
        self.survey_data = self._load_kb()
        # State tracking
        self.current_section_index = 0
        self.current_question_index = 0
        self.answers = {}
        self.is_completed = False
        
        # Flatten questions for easier navigation or keep hierarchical?
        # Keeping hierarchical to respect section ordering.
        self.sections = sorted(self.survey_data.get("sections", []), key=lambda x: x.get("order", 0))

    def _load_kb(self) -> Dict[str, Any]:
        try:
            with open(self.kb_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load knowledge base from {self.kb_path}: {e}")
            return {}

    def get_survey_metadata(self) -> str:
        """Returns a summary of the survey context."""
        meta = self.survey_data.get("metadata", {})
        return f"Survey: {self.survey_data.get('survey_id')} ({meta.get('constituency')})"

    def get_current_question(self) -> Optional[Dict[str, Any]]:
        """Returns the current question object or None if survey is complete."""
        if self.is_completed:
            return None
        
        if self.current_section_index < len(self.sections):
            section = self.sections[self.current_section_index]
            questions = section.get("questions", [])
            
            if self.current_question_index < len(questions):
                return questions[self.current_question_index]
        
        return None

    def get_next_question_text(self) -> str:
        """Returns the text of the next question to ask, or a completion message."""
        question = self.get_current_question()
        if question:
            q_text = question.get("text", "")
            options = question.get("options", [])
            if options:
                return f"{q_text}\nOptions: {', '.join(options)}"
            return q_text
        return "Survey completed. Thank you for your time."


    def submit_answer(self, answer_text: str) -> str:
        current_q = self.get_current_question()

        if not current_q:
            return "Survey is already completed."

        q_text = current_q.get("text","").strip()

        # Guardrail
        if answer_text.strip() == q_text:
            logger.warning("Model attempted to submit question text as answer. Ignoring.")
            return q_text

        key = current_q.get("key")
        self.answers[key] = answer_text

        logger.info(f"Answer recorded for {current_q.get('question_id')} ({key}): {answer_text}")

        self._advance_index()

        return self.get_next_question_text()

    '''def submit_answer(self, answer_text: str) -> str:
        """Saves the answer and moves to the next question. Returns the NEXT question text."""
        current_q = self.get_current_question()
        if not current_q:
            return "Survey is already completed."
        
        # Save answer
        q_id = current_q.get("question_id")
        key = current_q.get("key")
        self.answers[key] = answer_text
        
        logger.info(f"Answer recorded for {q_id} ({key}): {answer_text}")

        # Advance index
        self._advance_index()
        
        return self.get_next_question_text()'''

    def _advance_index(self):
        """Moves internal pointers to the next valid question."""
        if self.current_section_index >= len(self.sections):
            self.is_completed = True
            return

        section = self.sections[self.current_section_index]
        questions = section.get("questions", [])

        self.current_question_index += 1
        
        # If we reached end of current section's questions
        if self.current_question_index >= len(questions):
            self.current_section_index += 1
            self.current_question_index = 0
            
            # Check if we finished all sections
            if self.current_section_index >= len(self.sections):
                self.is_completed = True

    def get_all_answers(self) -> Dict[str, Any]:
        return self.answers
