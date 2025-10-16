# File: app.py
import eventlet
eventlet.monkey_patch()

import os
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'my-super-secret-bluffer-game-key!'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'bluffer.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

sid_to_name = {}

# Color schemes for up to 10 players
PLAYER_COLORS = [
    {'bg': 'bg-black', 'text': 'text-white', 'border': 'border-black'},           # Black with white text
    {'bg': 'bg-red-600', 'text': 'text-white', 'border': 'border-red-600'},       # Red with white text
    {'bg': 'bg-blue-600', 'text': 'text-white', 'border': 'border-blue-600'},     # Blue with white text
    {'bg': 'bg-green-600', 'text': 'text-white', 'border': 'border-green-600'},   # Green with white text
    {'bg': 'bg-purple-600', 'text': 'text-white', 'border': 'border-purple-600'}, # Purple with white text
    {'bg': 'bg-yellow-300', 'text': 'text-black', 'border': 'border-yellow-300'}, # Yellow bg with black text
    {'bg': 'bg-orange-400', 'text': 'text-black', 'border': 'border-orange-400'}, # Orange bg with black text
    {'bg': 'bg-pink-400', 'text': 'text-white', 'border': 'border-pink-400'},     # Pink with white text
    {'bg': 'bg-teal-500', 'text': 'text-white', 'border': 'border-teal-500'},     # Teal with white text
    {'bg': 'bg-lime-400', 'text': 'text-black', 'border': 'border-lime-400'},     # Lime bg with black text
]

def get_player_color(player_name, player_order):
    """Get color scheme for a player based on their position in player_order"""
    try:
        index = player_order.index(player_name)
        return PLAYER_COLORS[index % len(PLAYER_COLORS)]
    except (ValueError, IndexError):
        return PLAYER_COLORS[0]

# --- Database Models ---
class SecretWord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    topic = db.Column(db.String(100), nullable=False)
    word = db.Column(db.String(100), nullable=False, unique=True)

    def to_dict(self):
        return {"id": self.id, "topic": self.topic, "word": self.word}

# --- Game State Management ---
game_state = {
    "players": {},
    "player_order": [],
    "is_running": False,
    "secret_word": "",
    "word_history": [],
    "bluffer": None,
    "bluffer_guesses": 3,
    "bluffer_knows_word": False,
    "bluffer_guessed_this_turn": False,
    "clues": [],
    "current_turn_index": 0,
    "voting_open": False,
    "host_sid": None
}

# --- Helper Functions ---
def get_whos_turn():
    if not game_state["player_order"] or game_state["current_turn_index"] >= len(game_state["player_order"]):
        return None
    return game_state["player_order"][game_state["current_turn_index"]]
    
def add_word_to_history():
    if game_state["secret_word"] and game_state["secret_word"] not in game_state["word_history"]:
        game_state["word_history"].append(game_state["secret_word"])

def broadcast_game_state():
    state_for_clients = {
        "players": list(game_state["players"].keys()),
        "is_running": game_state["is_running"],
        "word_history": game_state["word_history"]
    }
    if game_state["is_running"]:
        # Add color information to clues
        clues_with_colors = []
        for clue in game_state["clues"]:
            color_scheme = get_player_color(clue['player'], game_state["player_order"])
            clues_with_colors.append({
                'player': clue['player'],
                'clue': clue['clue'],
                'color': color_scheme
            })
        
        state_for_clients.update({
            "players": game_state["player_order"],
            "clues": clues_with_colors,
            "whos_turn": get_whos_turn(),
            "voting_open": game_state["voting_open"],
            "secret_word": game_state["secret_word"],
            "bluffer": game_state["bluffer"]
        })
    socketio.emit('game_update', state_for_clients)

# --- HTTP Routes ---
@app.route('/')
def index(): 
    return render_template('index.html')

@app.route('/player')
def player(): 
    return render_template('player.html')

@app.route('/admin')
def admin(): 
    return render_template('admin.html')

# --- API Routes ---
@app.route('/api/words', methods=['GET'])
def get_words():
    try:
        words = SecretWord.query.all()
        return jsonify([w.to_dict() for w in words])
    except Exception as e:
        print(f"Error getting words: {e}")
        return jsonify([]), 500

@app.route('/api/words', methods=['POST'])
def add_word():
    try:
        data = request.get_json()
        if not data or not data.get('topic') or not data.get('word'):
            return jsonify({"error": "Topic and word are required."}), 400
        
        existing_word = SecretWord.query.filter_by(word=data['word'].strip().lower()).first()
        if existing_word:
            return jsonify({"error": "This word already exists."}), 409

        new_word = SecretWord(topic=data['topic'].strip(), word=data['word'].strip())
        db.session.add(new_word)
        db.session.commit()
        return jsonify(new_word.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error adding word: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/words/<int:word_id>', methods=['DELETE'])
def delete_word(word_id):
    try:
        word = SecretWord.query.get_or_404(word_id)
        db.session.delete(word)
        db.session.commit()
        return jsonify({"message": "Word deleted."})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting word: {e}")
        return jsonify({"error": "Server error"}), 500

@app.route('/api/topics', methods=['GET'])
def get_topics():
    try:
        topics = db.session.query(SecretWord.topic).distinct().all()
        return jsonify(['Random'] + sorted([topic[0] for topic in topics if topic[0]]))
    except Exception as e:
        print(f"Error getting topics: {e}")
        return jsonify(['Random']), 500

# --- SocketIO Event Handlers ---
@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    try:
        broadcast_game_state()
    except Exception as e:
        print(f"Error in connect: {e}")

@socketio.on('ping')
def handle_ping():
    try:
        emit('pong')
    except Exception as e:
        print(f"Error in ping: {e}")

@socketio.on('register_as_host')
def handle_register_host():
    try:
        print(f"=== HOST REGISTRATION ===")
        print(f"New host SID: {request.sid}")
        print(f"Old host SID: {game_state['host_sid']}")
        game_state["host_sid"] = request.sid
        print(f"Host registered successfully: {request.sid}")
        emit('host_registered', {'success': True})
    except Exception as e:
        print(f"Error in register_as_host: {e}")

@socketio.on('disconnect')
def handle_disconnect():
    try:
        print(f"Client disconnected: {request.sid}")
        if request.sid in sid_to_name:
            name = sid_to_name.pop(request.sid)
            if name in game_state["players"]:
                game_state["players"].pop(name)
                if name in game_state["player_order"]:
                    game_state["player_order"].remove(name)
                broadcast_game_state()
        if request.sid == game_state["host_sid"]:
            print("Host disconnected - clearing host_sid")
            game_state["host_sid"] = None
    except Exception as e:
        print(f"Error in disconnect: {e}")
        
@socketio.on('force_end_game')
def handle_force_end_game():
    try:
        if request.sid == game_state["host_sid"]:
            end_game("Host ended the game.")
    except Exception as e:
        print(f"Error in force_end_game: {e}")

@socketio.on('join_game')
def handle_join(data):
    try:
        name = data.get('name', '').strip()
        print(f"Player attempting to join: {name}")
        
        if not name:
            print(f"Join rejected for {name} - empty name")
            return emit('error', {'msg': 'Name is invalid or taken.'})
        
        if name in game_state["players"]:
            old_sid = game_state["players"][name]['sid']
            if old_sid != request.sid and old_sid in sid_to_name:
                print(f"Player {name} reconnecting - removing old connection")
                del sid_to_name[old_sid]
        
        game_state["players"][name] = {
            'sid': request.sid, 
            'is_bluffer': game_state["players"].get(name, {}).get('is_bluffer', False), 
            'voted_for': game_state["players"].get(name, {}).get('voted_for', None)
        }
        sid_to_name[request.sid] = name
        
        print(f"Player joined successfully: {name}")
        print(f"Total players: {list(game_state['players'].keys())}")
        emit('join_success', {'name': name})
        
        if game_state["is_running"] and name in game_state["players"]:
            role_data = {'is_bluffer': game_state["players"][name]['is_bluffer']}
            if game_state["players"][name]['is_bluffer'] and game_state.get("topic"):
                role_data['topic'] = game_state.get("topic", "Unknown")
            socketio.emit('role_info', role_data, room=request.sid)
        
        broadcast_game_state()
    except Exception as e:
        print(f"Error in join_game: {e}")
        emit('error', {'msg': 'Server error occurred'})

@socketio.on('start_game')
def handle_start_game(settings):
    try:
        print(f"=== START GAME CALLED ===")
        print(f"Request SID: {request.sid}")
        print(f"Host SID: {game_state['host_sid']}")
        print(f"Number of players: {len(game_state['players'])}")
        print(f"Players: {list(game_state['players'].keys())}")
        
        if request.sid != game_state["host_sid"]: 
            print("ERROR: Request SID does not match host SID!")
            return
        
        if len(game_state["players"]) < 3:
            print("ERROR: Not enough players")
            socketio.emit('error', {'msg': 'You need at least 3 players to start.'}, room=request.sid)
            return
        
        topic = settings.get('topic')
        print(f"Selected topic: {topic}")
        
        query = SecretWord.query
        if topic != 'Random':
            query = query.filter_by(topic=topic)
        
        all_words_for_topic = query.all()
        print(f"Words found for topic: {len(all_words_for_topic)}")
        
        if not all_words_for_topic:
            print("ERROR: No words found")
            socketio.emit('error', {'msg': 'No words found for this selection!'}, room=request.sid)
            return

        candidate_words = [w for w in all_words_for_topic if w.word not in game_state["word_history"]]
        if not candidate_words:
            topic_word_set = {w.word for w in all_words_for_topic}
            game_state["word_history"] = [w for w in game_state["word_history"] if w not in topic_word_set]
            candidate_words = all_words_for_topic

        selected_word_obj = random.choice(candidate_words)
        print(f"Selected word: {selected_word_obj.word}")
        
        current_player_names = list(game_state["players"].keys())
        random.shuffle(current_player_names)
        
        game_state["player_order"] = current_player_names
        game_state["bluffer"] = game_state["player_order"][0]
        game_state["current_turn_index"] = random.randint(0, len(current_player_names) - 1)
        game_state["is_running"] = True
        game_state["secret_word"] = selected_word_obj.word
        game_state["topic"] = selected_word_obj.topic
        game_state["clues"] = []
        game_state["voting_open"] = False
        game_state["bluffer_guesses"] = 3
        game_state["bluffer_knows_word"] = False
        game_state["bluffer_guessed_this_turn"] = False

        print(f"Game state updated. Bluffer: {game_state['bluffer']}")
        print(f"Player order: {game_state['player_order']}")
        print(f"Starting turn index: {game_state['current_turn_index']}")

        for name in game_state["player_order"]:
            player_data = game_state["players"][name]
            player_data['is_bluffer'] = (name == game_state["bluffer"])
            player_data['voted_for'] = None
            print(f"Sending role_info to {name} (is_bluffer: {player_data['is_bluffer']})")
            
            role_data = {'is_bluffer': player_data['is_bluffer']}
            if player_data['is_bluffer']:
                role_data['topic'] = selected_word_obj.topic
            
            socketio.emit('role_info', role_data, room=player_data['sid'])
        
        print("Broadcasting game state...")
        broadcast_game_state()
        print("=== START GAME COMPLETE ===")
    except Exception as e:
        print(f"Error in start_game: {e}")
        socketio.emit('error', {'msg': 'Server error occurred'}, room=request.sid)

@socketio.on('reveal_word_request')
def handle_reveal_request():
    try:
        name = sid_to_name.get(request.sid)
        if name and name in game_state["players"] and not game_state["players"][name].get('is_bluffer'):
            emit('reveal_word_answer', {'word': game_state['secret_word']})
    except Exception as e:
        print(f"Error in reveal_word_request: {e}")

@socketio.on('submit_clue')
def handle_submit_clue(data):
    try:
        name = sid_to_name.get(request.sid)
        clue = data.get('clue', '').strip()
        
        print(f"=== SUBMIT CLUE ===")
        print(f"Player: {name}, Clue: {clue}, Turn: {get_whos_turn()}")
        
        if name == get_whos_turn() and clue:
            print(f"Clue accepted!")
            game_state["clues"].append({'player': name, 'clue': clue})
            game_state["current_turn_index"] = (game_state["current_turn_index"] + 1) % len(game_state["player_order"])
            game_state["bluffer_guessed_this_turn"] = False
        
        print(f"About to broadcast after clue submission")
        broadcast_game_state()
        print(f"Broadcast complete")
    except Exception as e:
        print(f"Error in submit_clue: {e}")

@socketio.on('guess_word')
def handle_guess_word(data):
    try:
        name = sid_to_name.get(request.sid)
        guess = data.get('guess', '').strip().lower()
        
        if not (name == game_state["bluffer"] and 
                name == get_whos_turn() and 
                guess and 
                not game_state["bluffer_knows_word"] and
                not game_state["bluffer_guessed_this_turn"]):
            return emit('error', {'msg': 'You can only guess on your turn, once per turn!'})
        
        game_state["bluffer_guessed_this_turn"] = True
        
        if game_state["secret_word"].lower() == guess:
            game_state["bluffer_knows_word"] = True
            emit('guess_result', {'correct': True, 'msg': "You got it! Now keep bluffing!"})
        else:
            game_state["bluffer_guesses"] -= 1
            if game_state["bluffer_guesses"] > 0:
                emit('guess_result', {'correct': False, 'msg': f"WRONG! {game_state['bluffer_guesses']} guesses left."})
            else:
                end_game(f"The Bluffer, {name}, ran out of guesses!")
    except Exception as e:
        print(f"Error in guess_word: {e}")

@socketio.on('trigger_vote')
def handle_trigger_vote():
    try:
        if request.sid == game_state["host_sid"]:
            if game_state["bluffer_knows_word"]:
                end_game(f"The Bluffer ({game_state['bluffer']}) WINS! They knew the secret word and successfully bluffed everyone!")
                return
            
            game_state["voting_open"] = True
            broadcast_game_state()
    except Exception as e:
        print(f"Error in trigger_vote: {e}")

@socketio.on('submit_vote')
def handle_submit_vote(data):
    try:
        name = sid_to_name.get(request.sid)
        voted_for = data.get('player_name')
        
        if not (name and game_state["voting_open"] and voted_for):
             return
        game_state["players"][name]['voted_for'] = voted_for
        
        if all(p['voted_for'] for p in game_state["players"].values()):
            if game_state["bluffer_knows_word"]:
                end_game(f"The Bluffer ({game_state['bluffer']}) WINS! They knew the secret word and successfully bluffed everyone!")
                return
            
            votes = {}
            for p_name in game_state["players"]:
                vote = game_state["players"][p_name]['voted_for']
                if vote:
                    votes[vote] = votes.get(vote, 0) + 1
            
            if not votes: return
            voted_out_player = max(votes, key=votes.get)
            
            if voted_out_player == game_state["bluffer"]:
                end_game(f"The group correctly found the Bluffer! It was {game_state['bluffer']}.")
            else:
                end_game(f"{voted_out_player} was not the Bluffer. The real Bluffer was {game_state['bluffer']}.")
    except Exception as e:
        print(f"Error in submit_vote: {e}")

@app.route('/api/reset-game', methods=['POST'])
def reset_game():
    """Emergency reset endpoint"""
    try:
        global sid_to_name
        sid_to_name = {}
        
        game_state["players"] = {}
        game_state["player_order"] = []
        game_state["is_running"] = False
        game_state["secret_word"] = ""
        game_state["topic"] = ""
        game_state["bluffer"] = None
        game_state["bluffer_guesses"] = 3
        game_state["bluffer_knows_word"] = False
        game_state["bluffer_guessed_this_turn"] = False
        game_state["clues"] = []
        game_state["current_turn_index"] = 0
        game_state["voting_open"] = False
        game_state["host_sid"] = None
        
        broadcast_game_state()
        return jsonify({"message": "Game state reset successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def end_game(message_prefix):
    try:
        full_message = f"{message_prefix} The secret word was: {game_state['secret_word']}"
        add_word_to_history()
        
        global sid_to_name
        sid_to_name = {}
        
        game_state["players"] = {}
        game_state["player_order"] = []
        game_state["is_running"] = False
        game_state["secret_word"] = ""
        game_state["topic"] = ""
        game_state["bluffer"] = None
        game_state["bluffer_guesses"] = 3
        game_state["bluffer_knows_word"] = False
        game_state["bluffer_guessed_this_turn"] = False
        game_state["clues"] = []
        game_state["current_turn_index"] = 0
        game_state["voting_open"] = False
        
        socketio.emit('game_over', {'message': full_message})
        broadcast_game_state()
    except Exception as e:
        print(f"Error in end_game: {e}")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)

