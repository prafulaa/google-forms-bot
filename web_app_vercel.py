from flask import Flask, render_template, request, jsonify, session
import threading
import time
import uuid
import os
from form_bot import GoogleFormBot, BotConfig

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Store active bot sessions (in-memory - will reset on serverless cold starts)
active_bots = {}

class WebBotManager:
    def __init__(self):
        self.bot = None
        self.worker = None
        self.logs = []
        self.status = "idle"  # idle, running, completed, error
        self.submitted_count = 0
        self.target_count = 0
        
    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        if len(self.logs) > 100:  # Keep only last 100 logs
            self.logs.pop(0)
    
    def start_bot(self, form_url, count, headless, speed_mode):
        if self.worker and self.worker.is_alive():
            return False
            
        self.status = "running"
        self.target_count = count
        self.submitted_count = 0
        self.logs.clear()
        
        def run_worker():
            try:
                config = BotConfig(
                    form_url=form_url,
                    headless=headless,
                    speed_mode=speed_mode,
                )
                self.bot = GoogleFormBot(config, logger=self.log)
                self.log("Starting bot...")
                self.bot.start()
                submitted = self.bot.submit_n_responses(count)
                self.submitted_count = submitted
                self.log(f"Completed! Submitted {submitted} responses.")
                self.status = "completed"
            except Exception as e:
                self.log(f"ERROR: {str(e)}")
                self.status = "error"
            finally:
                if self.bot:
                    self.bot.quit()
        
        self.worker = threading.Thread(target=run_worker, daemon=True)
        self.worker.start()
        return True
    
    def stop_bot(self):
        if self.bot:
            self.log("Stop requested...")
            self.bot.stop()
            self.status = "stopping"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/start', methods=['POST'])
def start_bot():
    data = request.get_json()
    
    # Generate session ID if not exists
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    
    session_id = session['session_id']
    
    # Create or get bot manager
    if session_id not in active_bots:
        active_bots[session_id] = WebBotManager()
    
    bot_manager = active_bots[session_id]
    
    form_url = data.get('form_url', '').strip()
    count = int(data.get('count', 5))
    headless = data.get('headless', True)
    speed_mode = data.get('speed_mode', 'normal')
    
    # Validation
    if not form_url:
        return jsonify({'error': 'Please enter a Google Form URL'}), 400
    
    if count < 1 or count > 1000:
        return jsonify({'error': 'Responses must be between 1 and 1000'}), 400
    
    # Check if running on Vercel (serverless environment)
    if os.environ.get('VERCEL'):
        return jsonify({
            'error': 'Selenium automation is not supported in serverless environments. Please use a traditional hosting platform like Heroku or Railway for this application.'
        }), 400
    
    # Start the bot
    success = bot_manager.start_bot(form_url, count, headless, speed_mode)
    
    if success:
        return jsonify({'message': 'Bot started successfully', 'session_id': session_id})
    else:
        return jsonify({'error': 'Bot is already running'}), 400

@app.route('/api/stop', methods=['POST'])
def stop_bot():
    if 'session_id' not in session:
        return jsonify({'error': 'No active session'}), 400
    
    session_id = session['session_id']
    if session_id in active_bots:
        active_bots[session_id].stop_bot()
        return jsonify({'message': 'Stop requested'})
    
    return jsonify({'error': 'No active bot found'}), 400

@app.route('/api/status')
def get_status():
    if 'session_id' not in session:
        return jsonify({'error': 'No active session'}), 400
    
    session_id = session['session_id']
    if session_id not in active_bots:
        return jsonify({'error': 'No active bot found'}), 400
    
    bot_manager = active_bots[session_id]
    
    return jsonify({
        'status': bot_manager.status,
        'logs': bot_manager.logs,
        'submitted_count': bot_manager.submitted_count,
        'target_count': bot_manager.target_count
    })

@app.route('/api/clear')
def clear_session():
    if 'session_id' in session:
        session_id = session['session_id']
        if session_id in active_bots:
            bot_manager = active_bots[session_id]
            if bot_manager.bot:
                bot_manager.bot.quit()
            del active_bots[session_id]
    
    session.clear()
    return jsonify({'message': 'Session cleared'})

# Health check endpoint for Vercel
@app.route('/api/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'environment': 'vercel' if os.environ.get('VERCEL') else 'local',
        'message': 'Google Forms Auto-Responder is running'
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
