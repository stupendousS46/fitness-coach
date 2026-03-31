# Fitness Coach

A Gradio-based AI fitness coach using Mistral AI for personalized exercise recommendations.

## Features
- Personalized exercise suggestions based on goals, equipment, and difficulty
- 40+ exercises with video tutorials
- Clean web interface

## Setup
1. Install dependencies: `pip install requests gradio python-dotenv`
2. Add `MISTRAL_KEY=your-key` to `.env`
3. Run: `python fitness_coach.py`

## Usage
Start the app, open the local URL, and chat with the AI coach about your fitness goals.

## Recent Changes

- ✅ Moved exercise database to JSON file (`exercises.json`)
- ✅ Refactored code into clean, modular classes
- ✅ Removed hardcoded API key (now uses environment variables)
- ✅ Improved error handling and code organization
- ✅ Added proper documentation and type hints