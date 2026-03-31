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
from datetime import datetime
import re

load_dotenv()


class UserMemory:
    """Manages user memory across conversations."""

    def __init__(self, memory_file="user_memory.json"):
        self.memory_file = memory_file
        self.memory = self._load_memory()

    def _load_memory(self):
        """Load user memory from file."""
        try:
            with open(self.memory_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {
                "goals": [],
                "injuries": [],
                "equipment_available": [],
                "fitness_level": "beginner",
                "preferences": {},
                "last_updated": datetime.now().isoformat()
            }

    def _save_memory(self):
        """Save memory to file."""
        self.memory["last_updated"] = datetime.now().isoformat()
        with open(self.memory_file, 'w', encoding='utf-8') as f:
            json.dump(self.memory, f, indent=2, ensure_ascii=False)

    def update_goals(self, goals):
        """Update user fitness goals."""
        self.memory["goals"] = list(set(self.memory["goals"] + goals))
        self._save_memory()

    def update_injuries(self, injuries):
        """Update user injuries."""
        self.memory["injuries"] = list(set(self.memory["injuries"] + injuries))
        self._save_memory()

    def update_equipment(self, equipment):
        """Update available equipment."""
        self.memory["equipment_available"] = list(set(self.memory["equipment_available"] + equipment))
        self._save_memory()

    def set_fitness_level(self, level):
        """Set fitness level."""
        self.memory["fitness_level"] = level
        self._save_memory()

    def get_relevant_info(self):
        """Get relevant user information for context."""
        info = []
        if self.memory["goals"]:
            info.append(f"Goals: {', '.join(self.memory['goals'])}")
        if self.memory["injuries"]:
            info.append(f"Injuries to avoid: {', '.join(self.memory['injuries'])}")
        if self.memory["equipment_available"]:
            info.append(f"Available equipment: {', '.join(self.memory['equipment_available'])}")
        info.append(f"Fitness level: {self.memory['fitness_level']}")
        return " | ".join(info)


class ExerciseDatabase:
    """Manages the exercise database loaded from JSON."""

    def __init__(self, json_file="exercises.json"):
        self.exercises = self._load_exercises(json_file)
        self._build_search_index()

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

    def _build_search_index(self):
        """Build search index for efficient lookup."""
        self.search_index = {}
        for exercise in self.exercises:
            # Index by muscle groups
            muscle_groups = [mg.strip().lower() for mg in exercise['muscle_group'].split(',')]
            for mg in muscle_groups:
                if mg not in self.search_index:
                    self.search_index[mg] = []
                self.search_index[mg].append(exercise)

            # Index by equipment
            equipment = exercise['equipment'].lower()
            if equipment not in self.search_index:
                self.search_index[equipment] = []
            self.search_index[equipment].append(exercise)

            # Index by difficulty
            difficulty = exercise['difficulty'].lower()
            if difficulty not in self.search_index:
                self.search_index[difficulty] = []
            self.search_index[difficulty].append(exercise)

    def search_exercises(self, query, limit=10):
        """Search exercises using RAG-like approach."""
        query = query.lower()
        results = []
        seen = set()

        # Direct keyword matching
        for exercise in self.exercises:
            if query in exercise['name'].lower() or query in exercise['muscle_group'].lower():
                if exercise['name'] not in seen:
                    results.append(exercise)
                    seen.add(exercise['name'])

        # Category-based search
        if query in self.search_index:
            for exercise in self.search_index[query]:
                if exercise['name'] not in seen:
                    results.append(exercise)
                    seen.add(exercise['name'])

        # Fuzzy matching for muscle groups
        muscle_keywords = {
            'chest': ['chest', 'pecs', 'pectorals'],
            'back': ['back', 'lats', 'rhomboids', 'traps'],
            'legs': ['quads', 'hamstrings', 'glutes', 'calves', 'legs'],
            'shoulders': ['shoulders', 'delts', 'deltoids'],
            'arms': ['biceps', 'triceps', 'arms'],
            'core': ['abs', 'core', 'abdominals']
        }

        for category, keywords in muscle_keywords.items():
            if any(kw in query for kw in keywords):
                for exercise in self.search_index.get(category, []):
                    if exercise['name'] not in seen:
                        results.append(exercise)
                        seen.add(exercise['name'])

        return results[:limit]

    def get_exercises_by_criteria(self, muscle_groups=None, equipment=None, difficulty=None, limit=10):
        """Get exercises by specific criteria."""
        results = self.exercises

        if muscle_groups:
            mg_set = set(mg.lower() for mg in muscle_groups)
            results = [e for e in results if any(mg.lower() in e['muscle_group'].lower() for mg in mg_set)]

        if equipment:
            eq_set = set(eq.lower() for eq in equipment)
            results = [e for e in results if e['equipment'].lower() in eq_set]

        if difficulty:
            diff_set = set(diff.lower() for diff in difficulty)
            results = [e for e in results if e['difficulty'].lower() in diff_set]

        return results[:limit]

    def get_all_exercises(self):
        """Return all exercises."""
        return self.exercises

    def get_exercise_count(self):
        """Return the number of exercises in the database."""
        return len(self.exercises)


class IntentExtractor:
    """Extracts user intent from messages."""

    GOAL_KEYWORDS = {
        'strength': ['strength', 'muscle', 'build muscle', 'gain muscle', 'hypertrophy'],
        'cardio': ['cardio', 'fat loss', 'lose weight', 'weight loss', 'burn fat', 'endurance'],
        'core': ['abs', 'core', 'six pack', 'abdominals'],
        'full body': ['full body', 'general fitness', 'overall fitness'],
        'powerlifting': ['powerlifting', 'max strength', 'heavy lifting'],
        'bodybuilding': ['bodybuilding', 'aesthetics', 'muscle definition']
    }

    EQUIPMENT_KEYWORDS = {
        'bodyweight': ['no equipment', 'bodyweight', 'home workout', 'no gym'],
        'dumbbells': ['dumbbell', 'weights', 'free weights'],
        'barbell': ['barbell', 'squat rack', 'power rack'],
        'cable machine': ['cable', 'machine', 'gym equipment'],
        'resistance bands': ['bands', 'resistance bands'],
        'kettlebell': ['kettlebell', 'kettlebells'],
        'pull-up bar': ['pull-up bar', 'chin-up bar']
    }

    DIFFICULTY_KEYWORDS = {
        'beginner': ['easy', 'beginner', 'new', 'starter'],
        'intermediate': ['medium', 'intermediate', 'moderate'],
        'advanced': ['hard', 'advanced', 'expert', 'difficult']
    }

    INJURY_KEYWORDS = {
        'knee': ['knee', 'knees', 'acl', 'meniscus'],
        'back': ['back', 'spine', 'disc', 'herniated'],
        'shoulder': ['shoulder', 'rotator cuff', 'impingement'],
        'wrist': ['wrist', 'carpal tunnel'],
        'ankle': ['ankle', 'sprained ankle'],
        'elbow': ['elbow', 'tennis elbow']
    }

    def extract(self, message):
        """Extract intent from user message."""
        msg = message.lower()
        intent = {
            'goal': [],
            'equipment': [],
            'difficulty': [],
            'injuries': [],
            'muscle_groups': [],
            'workout_type': None,
            'duration': None
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

        # Extract injuries
        for injury, keywords in self.INJURY_KEYWORDS.items():
            if any(keyword in msg for keyword in keywords):
                intent['injuries'].append(injury)

        # Extract muscle groups mentioned
        muscle_groups = ['chest', 'back', 'legs', 'shoulders', 'biceps', 'triceps', 'quads', 'hamstrings', 'glutes', 'calves', 'abs', 'core']
        for mg in muscle_groups:
            if mg in msg:
                intent['muscle_groups'].append(mg)

        # Extract workout type
        if 'plan' in msg or 'program' in msg or 'routine' in msg:
            intent['workout_type'] = 'plan'
        elif 'single' in msg or 'one exercise' in msg:
            intent['workout_type'] = 'single'

        # Extract duration
        duration_match = re.search(r'(\d+)\s*(day|week|month)', msg)
        if duration_match:
            intent['duration'] = f"{duration_match.group(1)} {duration_match.group(2)}"

        return intent


class ExerciseFilter:
    """Filters exercises based on user intent and memory."""

    GOAL_TO_MUSCLES = {
        "strength": ["chest", "back", "legs", "shoulders", "biceps", "triceps"],
        "cardio": ["cardio", "full body"],
        "core": ["core", "abs"],
        "full body": ["full body"],
        "powerlifting": ["legs", "back", "chest", "shoulders"],
        "bodybuilding": ["chest", "back", "legs", "shoulders", "biceps", "triceps"]
    }

    INJURY_EXCLUSIONS = {
        'knee': ['squats', 'lunges', 'jumps', 'deep knee bends'],
        'back': ['deadlifts', 'heavy squats', 'overhead press'],
        'shoulder': ['overhead press', 'pull-ups', 'dips'],
        'wrist': ['push-ups', 'planks', 'heavy gripping'],
        'ankle': ['jumps', 'running', 'lunges'],
        'elbow': ['dips', 'push-ups', 'pull-ups']
    }

    def __init__(self, database, user_memory):
        self.database = database
        self.user_memory = user_memory

    def filter(self, intent, max_results=5):
        """Filter exercises based on intent and user memory."""
        candidates = []

        # Start with all exercises
        exercises = self.database.get_all_exercises()

        # Apply injury filters first (safety first)
        exercises = self._filter_injuries(exercises, intent.get('injuries', []))

        # Apply goal-based filtering
        if intent.get('goal'):
            exercises = self._filter_by_goals(exercises, intent['goal'])

        # Apply equipment filtering
        if intent.get('equipment'):
            exercises = self._filter_by_equipment(exercises, intent['equipment'])

        # Apply difficulty filtering
        if intent.get('difficulty'):
            exercises = self._filter_by_difficulty(exercises, intent['difficulty'])

        # Apply muscle group filtering
        if intent.get('muscle_groups'):
            exercises = self._filter_by_muscle_groups(exercises, intent['muscle_groups'])

        # If no exercises match, use search as fallback
        if not exercises:
            query = ' '.join(intent.get('goal', []) + intent.get('muscle_groups', []) + intent.get('equipment', []))
            exercises = self.database.search_exercises(query, max_results)

        # Rank and return top results
        return self._rank_exercises(exercises, intent)[:max_results]

    def _filter_injuries(self, exercises, injuries):
        """Filter out exercises that might aggravate injuries."""
        if not injuries:
            return exercises

        safe_exercises = []
        for exercise in exercises:
            exercise_name = exercise['name'].lower()
            is_safe = True

            for injury in injuries:
                if injury in self.INJURY_EXCLUSIONS:
                    excluded_moves = self.INJURY_EXCLUSIONS[injury]
                    if any(move in exercise_name for move in excluded_moves):
                        is_safe = False
                        break

            if is_safe:
                safe_exercises.append(exercise)

        return safe_exercises

    def _filter_by_goals(self, exercises, goals):
        """Filter exercises by fitness goals."""
        relevant_muscles = []
        for goal in goals:
            relevant_muscles.extend(self.GOAL_TO_MUSCLES.get(goal, []))

        if not relevant_muscles:
            return exercises

        filtered = []
        for exercise in exercises:
            exercise_muscles = [mg.strip().lower() for mg in exercise['muscle_group'].split(',')]
            if any(rm in exercise_muscles for rm in relevant_muscles):
                filtered.append(exercise)

        return filtered

    def _filter_by_equipment(self, exercises, equipment):
        """Filter exercises by available equipment."""
        if not equipment:
            return exercises

        filtered = []
        for exercise in exercises:
            exercise_eq = exercise['equipment'].lower()
            if any(eq.lower() in exercise_eq for eq in equipment):
                filtered.append(exercise)

        return filtered

    def _filter_by_difficulty(self, exercises, difficulties):
        """Filter exercises by difficulty level."""
        if not difficulties:
            return exercises

        filtered = []
        for exercise in exercises:
            if exercise['difficulty'].lower() in difficulties:
                filtered.append(exercise)

        return filtered

    def _filter_by_muscle_groups(self, exercises, muscle_groups):
        """Filter exercises by specific muscle groups."""
        if not muscle_groups:
            return exercises

        filtered = []
        for exercise in exercises:
            exercise_muscles = [mg.strip().lower() for mg in exercise['muscle_group'].split(',')]
            if any(mg in exercise_muscles for mg in muscle_groups):
                filtered.append(exercise)

        return filtered

    def _rank_exercises(self, exercises, intent):
        """Rank exercises based on relevance to intent."""
        if not exercises:
            return exercises

        scored = []
        for exercise in exercises:
            score = 0

            # Equipment availability bonus
            if intent.get('equipment'):
                exercise_eq = exercise['equipment'].lower()
                if any(eq.lower() in exercise_eq for eq in intent['equipment']):
                    score += 3

            # Goal relevance bonus
            if intent.get('goal'):
                exercise_muscles = [mg.strip().lower() for mg in exercise['muscle_group'].split(',')]
                for goal in intent['goal']:
                    relevant_muscles = self.GOAL_TO_MUSCLES.get(goal, [])
                    if any(rm in exercise_muscles for rm in relevant_muscles):
                        score += 2

            # Muscle group specificity bonus
            if intent.get('muscle_groups'):
                exercise_muscles = [mg.strip().lower() for mg in exercise['muscle_group'].split(',')]
                if any(mg in exercise_muscles for mg in intent['muscle_groups']):
                    score += 2

            scored.append((exercise, score))

        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        return [exercise for exercise, score in scored]


class WorkoutPlanner:
    """Generates workout plans based on user goals and constraints."""

    WORKOUT_TEMPLATES = {
        'strength': {
            'push': ['chest', 'shoulders', 'triceps'],
            'pull': ['back', 'biceps'],
            'legs': ['quads', 'hamstrings', 'glutes', 'calves']
        },
        'bodybuilding': {
            'chest': ['chest'],
            'back': ['back'],
            'legs': ['quads', 'hamstrings', 'glutes'],
            'shoulders': ['shoulders'],
            'arms': ['biceps', 'triceps']
        },
        'full_body': {
            'full_body': ['full body', 'chest', 'back', 'legs', 'shoulders']
        }
    }

    def __init__(self, database, user_memory):
        self.database = database
        self.user_memory = user_memory

    def generate_plan(self, intent, days=3):
        """Generate a workout plan."""
        goals = intent.get('goal', ['strength'])
        primary_goal = goals[0] if goals else 'strength'

        template = self.WORKOUT_TEMPLATES.get(primary_goal, self.WORKOUT_TEMPLATES['strength'])

        plan = {}
        day_names = ['Day 1', 'Day 2', 'Day 3', 'Day 4', 'Day 5', 'Day 6', 'Day 7']

        for i, (day_type, muscle_groups) in enumerate(list(template.items())[:days]):
            day_name = day_names[i] if i < len(day_names) else f"Day {i+1}"

            exercises = []
            for mg in muscle_groups:
                # Get exercises for this muscle group
                mg_exercises = self.database.search_exercises(mg, limit=3)

                # Filter by equipment and injuries
                available_equipment = self.user_memory.memory.get('equipment_available', [])
                injuries = self.user_memory.memory.get('injuries', [])

                filtered = []
                for ex in mg_exercises:
                    # Check equipment
                    if available_equipment and ex['equipment'].lower() not in [eq.lower() for eq in available_equipment]:
                        continue

                    # Check injuries
                    exercise_name = ex['name'].lower()
                    safe = True
                    for injury in injuries:
                        if injury in ExerciseFilter.INJURY_EXCLUSIONS:
                            excluded = ExerciseFilter.INJURY_EXCLUSIONS[injury]
                            if any(move in exercise_name for move in excluded):
                                safe = False
                                break
                    if safe:
                        filtered.append(ex)

                exercises.extend(filtered[:2])  # 2 exercises per muscle group

            plan[day_name] = exercises

        return plan

    def format_plan(self, plan):
        """Format workout plan for display."""
        formatted = "📋 Your Workout Plan:\n\n"

        for day, exercises in plan.items():
            formatted += f"🏋️ {day}:\n"
            for i, exercise in enumerate(exercises, 1):
                formatted += f"  {i}. {exercise['name']} ({exercise['equipment']})\n"
                formatted += f"     💪 {exercise['muscle_group']} | {exercise['difficulty']}\n"
                formatted += f"     🎥 {exercise['video_url']}\n"
            formatted += "\n"

        return formatted


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
        self.user_memory = UserMemory()
        self.intent_extractor = IntentExtractor()
        self.filter = ExerciseFilter(self.database, self.user_memory)
        self.planner = WorkoutPlanner(self.database, self.user_memory)
        self.ai_model = MistralModel()

    def _update_memory_from_intent(self, intent):
        """Update user memory based on extracted intent."""
        if intent.get('goal'):
            self.user_memory.update_goals(intent['goal'])

        if intent.get('injuries'):
            self.user_memory.update_injuries(intent['injuries'])

        if intent.get('equipment'):
            self.user_memory.update_equipment(intent['equipment'])

        if intent.get('difficulty'):
            fitness_level = intent['difficulty'][0] if intent['difficulty'] else 'beginner'
            self.user_memory.set_fitness_level(fitness_level)

    def _format_exercises_for_ai(self, exercises):
        """Format exercises for AI prompt."""
        text = "Exercises:\n"
        for i, exercise in enumerate(exercises, 1):
            text += f"{i}. {exercise['name']} ({exercise['video_url']})\n"
        return text

    def _build_prompt(self, conversation_history, user_message, exercises, intent, workout_plan=None):
        """Build the AI prompt."""
        formatted_exercises = self._format_exercises_for_ai(exercises)

        # Format conversation history
        history_text = ""
        for entry in conversation_history[-4:]:
            history_text += f"{entry['role']}: {entry['content']}\n"

        user_context = self.user_memory.get_relevant_info()

        prompt = f"""
You are BubbyTrainer, a strict fitness coach with memory of user preferences.

USER CONTEXT: {user_context}

Rules:
- Max 2 sentences
- MUST suggest 1-2 exercises from list OR workout plan if requested
- Format: Exercise Name (URL)
- For plans: Use the provided workout plan format
- MUST end with a question
- No emojis in responses, no fluff
- Consider user injuries and equipment limitations

ONLY use exercises from this list:
{formatted_exercises}
"""

        if workout_plan:
            prompt += f"\nWORKOUT PLAN:\n{workout_plan}"

        prompt += f"""

Conversation:
{history_text}

User: {user_message}
"""

        return prompt

    def generate_response(self, conversation_history, user_message):
        """Generate a coach response for the user message."""
        if not user_message.strip():
            return ""

        # Extract intent
        intent = self.intent_extractor.extract(user_message)

        # Update memory with new information
        self._update_memory_from_intent(intent)

        # Check if user wants a workout plan
        if intent.get('workout_type') == 'plan' or 'plan' in user_message.lower() or 'program' in user_message.lower():
            plan = self.planner.generate_plan(intent)
            formatted_plan = self.planner.format_plan(plan)

            prompt = self._build_prompt(conversation_history, user_message, [], intent, formatted_plan)
            return self.ai_model.generate_response(prompt)
        else:
            # Filter exercises for single recommendations
            exercises = self.filter.filter(intent)

            # Build prompt
            prompt = self._build_prompt(conversation_history, user_message, exercises, intent)

            # Generate response
            return self.ai_model.generate_response(prompt)

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
with gr.Blocks(title="BubbyTrainer - AI Fitness Coach") as demo:
    gr.Markdown("# 🏋️ BubbyTrainer")
    gr.Markdown("*Your AI fitness coach with memory and smart recommendations*")

    with gr.Row():
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(
                value=[{
                    "role": "assistant",
                    "content": "hey! i'm bubbytrainer. what's your fitness goal? i remember your preferences between sessions."
                }],
                height=500,
                show_label=False
            )

            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Tell me your goal, equipment, or ask for a workout plan...",
                    scale=4,
                    show_label=False
                )
                send = gr.Button("Send", scale=1, variant="primary")

        with gr.Column(scale=1):
            gr.Markdown("### Your Profile")
            memory_display = gr.Textbox(
                value=coach.user_memory.get_relevant_info(),
                label="Current Memory",
                lines=8,
                interactive=False
            )

            clear_memory = gr.Button("Reset Memory", size="sm")
            clear_chat_btn = gr.Button("Clear Chat", size="sm")

    def update_memory_display():
        return coach.user_memory.get_relevant_info()

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

        return "", chat_history, update_memory_display()

    def clear_chat():
        """Clear the chat history."""
        return [{
            "role": "assistant",
            "content": "hey! i'm bubbytrainer. what's your fitness goal? i remember your preferences between sessions."
        }], "", update_memory_display()

    def reset_memory():
        """Reset user memory."""
        coach.user_memory.memory = {
            "goals": [],
            "injuries": [],
            "equipment_available": [],
            "fitness_level": "beginner",
            "preferences": {},
            "last_updated": datetime.now().isoformat()
        }
        coach.user_memory._save_memory()
        return update_memory_display()

    send.click(respond, inputs=[msg, chatbot], outputs=[msg, chatbot, memory_display])
    msg.submit(respond, inputs=[msg, chatbot], outputs=[msg, chatbot, memory_display])
    clear_chat_btn.click(clear_chat, outputs=[chatbot, msg, memory_display])
    clear_memory.click(reset_memory, outputs=[memory_display])

if __name__ == "__main__":
    # Add health check endpoint
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class HealthCheckHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/health':
                self.send_response(200)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b'OK')
            else:
                self.send_response(404)
                self.end_headers()

    def run_health_server():
        server = HTTPServer(('0.0.0.0', 8000), HealthCheckHandler)
        server.serve_forever()

    # Start health check server in background
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()

    demo.launch(server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft())