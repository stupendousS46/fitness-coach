# -*- coding: utf-8 -*-
"""
Fitness Coach Application

A Gradio-based fitness coach that uses Mistral AI to provide personalized
exercise recommendations based on user goals, equipment, and difficulty level.
"""

import json
import os
import time
import requests
import gradio as gr
from dotenv import load_dotenv

load_dotenv()


class ExerciseDatabase:
    """Manages the exercise database loaded from JSON."""

    def __init__(self, json_file="exercises.json"):
        self.exercises = self._load_exercises(json_file)

    def _load_exercises(self, json_file):
        """Load exercises from JSON file."""
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Error: {json_file} not found.")
            return []
        except json.JSONDecodeError:
            print(f"Error: Invalid JSON in {json_file}.")
            return []

    def get_all_exercises(self):
        """Return all exercises."""
        return self.exercises

    def get_exercise_count(self):
        """Return the number of exercises in the database."""
        return len(self.exercises)


class IntentExtractor:
    """Extracts user intent from messages."""

    GOAL_KEYWORDS = {
        'strength': ['strength', 'muscle', 'build'],
        'cardio': ['cardio', 'fat', 'lose weight', 'belly'],
        'core': ['abs', 'core'],
        'full body': ['full body']
    }

    EQUIPMENT_KEYWORDS = {
        'bodyweight': ['no equipment', 'bodyweight'],
        'dumbbells': ['dumbbell'],
        'barbell': ['barbell']
    }

    DIFFICULTY_KEYWORDS = {
        'easy': ['easy', 'beginner'],
        'medium': ['medium'],
        'hard': ['hard']
    }

    def extract(self, message):
        """Extract intent from user message."""
        msg = message.lower()
        intent = {
            'goal': [],
            'equipment': [],
            'difficulty': []
        }

        # Extract goals
        for goal, keywords in self.GOAL_KEYWORDS.items():
            if any(keyword in msg for keyword in keywords):
                intent['goal'].append(goal)

        # Extract equipment
        for equipment, keywords in self.EQUIPMENT_KEYWORDS.items():
            if any(keyword in msg for keyword in keywords):
                intent['equipment'].append(equipment)

        # Extract difficulty
        for difficulty, keywords in self.DIFFICULTY_KEYWORDS.items():
            if any(keyword in msg for keyword in keywords):
                intent['difficulty'].append(difficulty)

        return intent


class ExerciseFilter:
    """Filters exercises based on user intent."""

    GOAL_MAP = {
        "strength": ["chest", "back", "legs", "shoulders", "biceps", "triceps"],
        "cardio": ["cardio", "full body"],
        "core": ["core"],
        "full body": ["full body"]
    }

    def __init__(self, database):
        self.database = database

    def filter(self, intent):
        """Filter exercises based on intent."""
        results = []

        for exercise in self.database.get_all_exercises():
            if self._matches_intent(exercise, intent):
                results.append(exercise)

        # Fallback to first 5 exercises if no matches
        if not results:
            results = self.database.get_all_exercises()[:5]

        return results[:5]

    def _matches_intent(self, exercise, intent):
        """Check if exercise matches the user's intent."""
        muscle_group = exercise['muscle_group'].lower()
        equipment = exercise['equipment'].lower()
        difficulty = exercise['difficulty'].lower()

        # Goal match
        goal_match = (
            not intent['goal'] or
            any(any(gm.lower() in muscle_group for gm in self.GOAL_MAP.get(g, []))
                for g in intent['goal'])
        )

        # Equipment match
        equipment_match = (
            not intent['equipment'] or
            any(eq.lower() in equipment for eq in intent['equipment'])
        )

        # Difficulty match
        difficulty_match = (
            not intent['difficulty'] or
            difficulty in intent['difficulty']
        )

        return goal_match and equipment_match and difficulty_match


class MistralModel:
    """Handles Mistral AI model interactions."""

    def __init__(self):
        self.api_key = None
        self.endpoint = "https://api.mistral.ai/v1/chat/completions"
        self.model_name = "mistral-medium"
        self._setup_model()

    def _setup_model(self):
        """Setup the Mistral model."""
        self.api_key = os.getenv("MISTRAL_KEY")
        if not self.api_key:
            print("Warning: MISTRAL_KEY environment variable not set.")

    def generate_response(self, prompt):
        """Generate a response using the model."""
        if not self.api_key:
            return "AI model not available. Please check your API key."

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        data = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
            "temperature": 0.7
        }

        for attempt in range(3):
            try:
                response = requests.post(self.endpoint, headers=headers, json=data, timeout=30)
                response.raise_for_status()
                result = response.json()
                if "choices" in result and result["choices"]:
                    return result["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"Generation attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    time.sleep(2)

        return "AI service is currently busy. Please try again."


class FitnessCoach:
    """Main fitness coach application logic."""

    def __init__(self):
        self.database = ExerciseDatabase()
        self.intent_extractor = IntentExtractor()
        self.filter = ExerciseFilter(self.database)
        self.ai_model = MistralModel()

    def _format_exercises_for_ai(self, exercises):
        """Format exercises for AI prompt."""
        text = "Exercises:\n"
        for i, exercise in enumerate(exercises, 1):
            text += f"{i}. {exercise['name']} ({exercise['video_url']})\n"
        return text

    def _build_prompt(self, conversation_history, user_message, exercises):
        """Build the AI prompt."""
        formatted_exercises = self._format_exercises_for_ai(exercises)

        # Format conversation history
        history_text = ""
        for entry in conversation_history[-4:]:
            history_text += f"{entry['role']}: {entry['content']}\n"

        return f"""
You are BubbyTrainer, a strict fitness coach.

Rules:
- Max 2 sentences
- MUST suggest 1-2 exercises from list
- Format: Exercise Name (URL)
- MUST end with a question
- No emojis, no fluff

ONLY use exercises from this list:
{formatted_exercises}

Conversation:
{history_text}

User: {user_message}
"""

    def generate_response(self, conversation_history, user_message):
        """Generate a coach response for the user message."""
        if not user_message.strip():
            return ""

        # Extract intent
        intent = self.intent_extractor.extract(user_message)

        # Filter exercises
        exercises = self.filter.filter(intent)

        # Build prompt
        prompt = self._build_prompt(conversation_history, user_message, exercises)

        # Generate response
        return self.ai_model.generate_response(prompt)


# Initialize the coach
coach = FitnessCoach()

def respond(user_message, chat_history):
    """Handle user message and return response."""
    reply = coach.generate_response(chat_history, user_message)

    chat_history.append({
        "role": "user",
        "content": user_message
    })

    chat_history.append({
        "role": "assistant",
        "content": reply
    })

    return "", chat_history


def clear_chat():
    """Clear the chat history."""
    return [{
        "role": "assistant",
        "content": "hey! where do you wanna start today??"
    }], ""


# Gradio Interface
with gr.Blocks() as demo:
    gr.Markdown("## BubbyTrainer")
    gr.Markdown("*No fluff. Just results.*")

    chatbot = gr.Chatbot(
        value=[{
            "role": "assistant",
            "content": "hey! where do you wanna start today??"
        }],
        height=500
    )

    with gr.Row():
        msg = gr.Textbox(placeholder="Tell me your goal...", scale=4)
        send = gr.Button("Send", scale=1)

    clear = gr.Button("Clear")

    send.click(respond, inputs=[msg, chatbot], outputs=[msg, chatbot])
    msg.submit(respond, inputs=[msg, chatbot], outputs=[msg, chatbot])
    clear.click(clear_chat, outputs=[chatbot, msg])

if __name__ == "__main__":
    demo.launch()