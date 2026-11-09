
import os
import random
import string
import threading

import chess
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY", "change-this-secret"
)
socketio = SocketIO(app, cors_allowed_origins=[])

lock = threading.RLock()
rooms = {}
POWER_NAMES = ["TARCZA", "BŁYSKAWICA", "TELEPORT", "OGIEŃ", "AURA"]

VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000,
}

def new_code():
    return "".join(random.choices(
        string.ascii_uppercase + string.digits, k=6
    ))

def power_color(power):
    return {
        "TARCZA": "#459cff",
        "BŁYSKAWICA": "#f6c945",
        "TELEPORT": "#ae70ff",
        "OGIEŃ": "#ff694b",
        "AURA": "#40cfa5",
    }[power]

def new_game():
    board = chess.Board()
    pieces = {}

    # Każda z 32 figur otrzymuje własną moc.
    for square in board.piece_map():
        pieces[chess.square_name(square)] = {
            "power": random.choice(POWER_NAMES),
            "used": False,
            "shield": False,
            "aura": False,
        }

    return {
        "board": board,
        "pieces": pieces,
        "players": {},
        "mode": "online",
        "difficulty": 2,
        "status": "waiting",
        "winner": None,
        "history": [],
    }

def piece_power(game, square):
    return game["pieces"].get(chess.square_name(square))

def aura_protected(game, target):
    piece = game["board"].piece_at(target)
    if not piece:
        return False

    for name, data in game["pieces"].items():
        if not data["aura"]:
            continue

        source = chess.parse_square(name)
        aura_piece = game["board"].piece_at(source)

        if not aura_piece or aura_piece.color != piece.color:
            continue

        distance = max(
            abs(chess.square_file(source) -
                chess.square_file(target)),
            abs(chess.square_rank(source) -
                chess.square_rank(target))
        )

        if distance <= 1 and distance > 0:
            return True

    return False

def protected(game, square):
    data = piece_power(game, square)
    return bool(
        (data and data["shield"]) or
        aura_protected(game, square)
    )

def serialize(game):
    board = game["board"]
    legal = []

    if game["status"] == "playing":
        legal = [m.uci() for m in board.legal_moves]

    return {
        "fen": board.fen(),
        "turn": "white" if board.turn else "black",
        "pieces": game["pieces"],
        "legal": legal,
        "status": game["status"],
        "result": board.result() if board.is_game_over() else None,
        "check": board.is_check(),
        "history": game["history"][-20:],
        "players": {
            color: {
                "name": player["name"],
                "connected": player["connected"],
            }
            for color, player in game["players"].items()
        },
        "mode": game["mode"],
        "difficulty": game["difficulty"],
    }

def publish(code):
    game = rooms[code]
    socketio.emit("state", serialize(game), to=code)

def finish_if_needed(game):
    if game["board"].is_game_over():
        game["status"] = "finished"
        result = game["board"].result()
        game["winner"] = (
            "white" if result == "1-0"
            else "black" if result == "0-1"
            else None
        )

def move_piece(game, uci, color):
    board = game["board"]

    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return False, "Niepoprawny zapis ruchu."

    if board.turn != (color == "white"):
        return False, "Nie twoja kolej."

    if move not in board.legal_moves:
        return False, "Nielegalny ruch."

    victim = board.piece_at(move.to_square)

    if victim and protected(game, move.to_square):
        return False, "Ta figura jest chroniona."

    source_name = chess.square_name(move.from_square)
    target_name = chess.square_name(move.to_square)

    data = game["pieces"].pop(source_name, None)

    if victim:
        game["pieces"].pop(target_name, None)

    board.push(move)

    if data:
        game["pieces"][target_name] = data

    power = data["power"] if data else None

    game["history"].append({
        "move": uci,
        "power": power,
        "color": color,
    })

    # Błyskawica: po jednym ruchu pozwala na drugi ruch.
    if data and power == "BŁYSKAWICA" and not data["used"]:
        data["used"] = True
        board.turn = not board.turn
        game["history"].append({
            "move": "⚡ Dodatkowy ruch",
            "power": power,
            "color": color,
        })

    finish_if_needed(game)
    return True, "Ruch wykonany."

def activate(game, color, square_name):
    board = game["board"]

    try:
        square = chess.parse_square(square_name)
    except ValueError:
        return False, "Nieprawidłowe pole."

    piece = board.piece_at(square)
    data = game["pieces"].get(square_name)

    if not piece or piece.color != (color == "white"):
        return False, "Nie możesz użyć tej figury."

    if not data:
        return False, "Brak supermocy."

    if data["used"]:
        return False, "Ta moc została już wykorzystana."

    power = data["power"]

    if power == "TARCZA":
        data["shield"] = True
        data["used"] = True

    elif power == "AURA":
        data["aura"] = True
        data["used"] = True

    elif power in ("TELEPORT", "OGIEŃ"):
        return True, "Wybierz pole docelowe."

    else:
        return True, "Błyskawica aktywuje się po ruchu."

    return True, "Supermoc aktywowana."

def special_action(game, color, source_name, target_name):
    board = game["board"]

    try:
        source = chess.parse_square(source_name)
        target = chess.parse_square(target_name)
    except ValueError:
        return False, "Nieprawidłowe pole."

    piece = board.piece_at(source)
    data = game["pieces"].get(source_name)

    if not piece or piece.color != (color == "white"):
        return False, "Nieprawidłowa figura."

    if not data or data["used"]:
        return False, "Brak dostępnej supermocy."

    power = data["power"]

    if power == "TELEPORT":
        if board.piece_at(target):
            return False, "Pole musi być puste."

        # Król i wieża nie teleportują się.
        # Dzięki temu nie obchodzimy zasad roszady.
        if piece.piece_type in (chess.KING, chess.ROOK):
            return False, "Król i wieża nie mogą teleportować."

        test = board.copy()
        test.remove_piece_at(source)
        test.set_piece_at(target, piece)

        if test.is_check():
            return False, "Teleport pozostawiłby króla w szachu."

        board.remove_piece_at(source)
        board.set_piece_at(target, piece)

        board.ep_square = None
        board.halfmove_clock += 1
        board.turn = not board.turn
        board.clear_stack()

        data["used"] = True
        game["pieces"][target_name] = data
        del game["pieces"][source_name]

    elif power == "OGIEŃ":
        victim = board.piece_at(target)

        if not victim or victim.color == piece.color:
            return False, "Wybierz figurę przeciwnika."

        if victim.piece_type == chess.KING:
            return False, "Ogień nie może zbijać króla."

        distance = max(
            abs(chess.square_file(source) -
                chess.square_file(target)),
            abs(chess.square_rank(source) -
                chess.square_rank(target))
        )

        if distance > 2:
            return False, "Cel jest za daleko."

        if protected(game, target):
            return False, "Cel jest chroniony."

        test = board.copy()
        test.remove_piece_at(target)

        if test.is_check():
            return False, "Nie możesz odsłonić własnego króla."

        board.remove_piece_at(target)
        game["pieces"].pop(target_name, None)
        data["used"] = True
        board.turn = not board.turn
        board.ep_square = None
        board.halfmove_clock = 0
        board.clear_stack()

    else:
        return False, "Ta moc nie obsługuje celu."

    game["history"].append({
        "move": f"{power}: {source_name} → {target_name}",
        "power": power,
        "color": color,
    })

    finish_if_needed(game)
    return True, "Supermoc wykonana!"

# =========================
# AI
# =========================

def evaluate(board):
    if board.is_checkmate():
        return -100000 if board.turn else 100000

    score = 0

    for square, piece in board.piece_map().items():
        value = VALUES[piece.piece_type]
        center = 7 - (
            abs(3.5 - chess.square_file(square)) +
            abs(3.5 - chess.square_rank(square))
        )
        value += center * 5
        score += value if piece.color else -value

    return score

def minimax(board, depth, alpha, beta, maximizing):
    if depth == 0 or board.is_game_over():
        return evaluate(board), None

    best_move = None
    moves = list(board.legal_moves)

    if maximizing:
        best_score = -float("inf")
        for move in moves:
            board.push(move)
            score, _ = minimax(
                board, depth - 1, alpha, beta, False
            )
            board.pop()

            if score > best_score:
                best_score = score
                best_move = move

            alpha = max(alpha, best_score)
            if beta <= alpha:
                break

    else:
        best_score = float("inf")
        for move in moves:
            board.push(move)
            score, _ = minimax(
                board, depth - 1, alpha, beta, True
            )
            board.pop()

            if score < best_score:
                best_score = score
                best_move = move

            beta = min(beta, best_score)
            if beta <= alpha:
                break

    return best_score, best_move


def ai_play(game):
    board = game["board"]

    # Komputer gra czarnymi
    if board.is_game_over() or board.turn:
        return

    depth = {
        1: 1,
        2: 2,
        3: 3,
    }.get(game["difficulty"], 2)

    _, move = minimax(
        board,
        depth,
        -float("inf"),
        float("inf"),
        False
    )

    # AI nie próbuje zbijać figur chronionych
    if move:
        victim = board.piece_at(move.to_square)

        if victim and protected(game, move.to_square):
            alternatives = [
                m for m in board.legal_moves
                if not (
                    board.piece_at(m.to_square)
                    and protected(game, m.to_square)
                )
            ]
            move = random.choice(alternatives) if alternatives else None

    if move:
        move_piece(game, move.uci(), "black")


# =========================
# STRONA I LOBBY
# =========================

@app.route("/")
def index():
    return render_template("index.html")

@socketio.on("create")
def create(data):
    with lock:
        code = new_code()
        while code in rooms:
            code = new_code()

        game = new_game()
        game["mode"] = data.get("mode", "online")
        game["difficulty"] = int(data.get("difficulty", 2))

        name = str(data.get("name", "Gracz"))[:20]

        game["players"]["white"] = {
            "sid": request.sid,
            "name": name,
            "connected": True,
        }

        if game["mode"] == "ai":
            game["players"]["black"] = {
                "sid": "AI",
                "name": "Chaos AI",
                "connected": True,
            }
            game["status"] = "playing"

        rooms[code] = game
        join_room(code)

        emit("created", {"code": code, "color": "white"})
        publish(code)

@socketio.on("join")
def join(data):
    with lock:
        code = str(data.get("code", "")).upper()
        game = rooms.get(code)

        if not game or game["mode"] != "online":
            emit("error_msg", "Nie znaleziono lobby.")
            return

        if "black" in game["players"]:
            emit("error_msg", "Lobby jest pełne.")
            return

        name = str(data.get("name", "Gracz"))[:20]

        game["players"]["black"] = {
            "sid": request.sid,
            "name": name,
            "connected": True,
        }

        game["status"] = "playing"
        join_room(code)

        emit("joined", {"code": code, "color": "black"})
        publish(code)

@socketio.on("move")
def on_move(data):
    with lock:
        code = data.get("code")
        game = rooms.get(code)

        if not game or game["status"] != "playing":
            return

        color = next(
            (
                c for c, p in game["players"].items()
                if p["sid"] == request.sid
            ),
            None
        )

        if color is None:
            return

        ok, message = move_piece(game, data.get("uci", ""), color)
        emit("action_result", {"ok": ok, "message": message})

        publish(code)

        if (
            game["mode"] == "ai"
            and game["status"] == "playing"
            and game["board"].turn is False
        ):
            ai_play(game)
            publish(code)

@socketio.on("power")
def on_power(data):
    with lock:
        code = data.get("code")
        game = rooms.get(code)

        if not game or game["status"] != "playing":
            return

        color = next(
            (
                c for c, p in game["players"].items()
                if p["sid"] == request.sid
            ),
            None
        )

        if color is None:
            return

        ok, message = activate(
            game, color, data.get("square", "")
        )

        emit("action_result", {"ok": ok, "message": message})
        publish(code)

@socketio.on("special")
def on_special(data):
    with lock:
        code = data.get("code")
        game = rooms.get(code)

        if not game or game["status"] != "playing":
            return

        color = next(
            (
                c for c, p in game["players"].items()
                if p["sid"] == request.sid
            ),
            None
        )

        if color is None:
            return

        ok, message = special_action(
            game, color,
            data.get("source", ""),
            data.get("target", "")
        )

        emit("action_result", {"ok": ok, "message": message})
        publish(code)

@socketio.on("disconnect")
def disconnect():
    with lock:
        for game in rooms.values():
            for player in game["players"].values():
                if player["sid"] == request.sid:
                    player["connected"] = False

# =========================
# URUCHOMIENIE
# =========================

if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
