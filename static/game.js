
const socket = io();

const $ = id => document.getElementById(id);

let code = null;
let myColor = null;
let state = null;
let selected = null;
let pendingPower = null;

const GLYPHS = {
    K:"♔", Q:"♕", R:"♖", B:"♗", N:"♘", P:"♙",
    k:"♚", q:"♛", r:"♜", b:"♝", n:"♞", p:"♟"
};

const POWER = {
    "TARCZA": ["🛡", "#459cff", "Tarcza"],
    "BŁYSKAWICA": ["⚡", "#f6c945", "Błyskawica"],
    "TELEPORT": ["🌀", "#ae70ff", "Teleport"],
    "OGIEŃ": ["🔥", "#ff694b", "Ogień"],
    "AURA": ["✦", "#40cfa5", "Aura"]
};

function show(id) {
    ["menu", "waiting", "game"].forEach(x =>
        $(x).classList.toggle("hidden", x !== id)
    );
}

function playerName() {
    return $("playerName").value.trim() || "Gracz";
}

function create(mode) {
    socket.emit("create", {
        mode,
        difficulty: Number($("difficulty").value),
        name: playerName()
    });
}

$("aiBtn").onclick = () => create("ai");
$("createBtn").onclick = () => create("online");

$("joinBtn").onclick = () => {
    socket.emit("join", {
        code: $("roomCode").value.trim().toUpperCase(),
        name: playerName()
    });
};

$("copyBtn").onclick = async () => {
    await navigator.clipboard.writeText(code);
    $("copyBtn").textContent = "SKOPIOWANO!";
};

$("newGame").onclick = () => {
    location.reload();
};

socket.on("created", data => {
    code = data.code;
    myColor = data.color;

    $("roomCodeDisplay").textContent = code;
    show("waiting");
});

socket.on("joined", data => {
    code = data.code;
    myColor = data.color;
});

socket.on("error_msg", message => {
    alert(message);
});

socket.on("action_result", data => {
    $("message").textContent = data.message;

    if (!data.ok) {
        pendingPower = null;
        $("cancelPower").classList.add("hidden");
    } else if (
        data.message === "Wybierz pole docelowe."
    ) {
        pendingPower = selected;
        $("cancelPower").classList.remove("hidden");
    } else {
        pendingPower = null;
        $("cancelPower").classList.add("hidden");
    }
});

$("cancelPower").onclick = () => {
    pendingPower = null;
    $("cancelPower").classList.add("hidden");
    render();
};

$("usePower").onclick = () => {
    if (!selected || !code || !state) return;

    socket.emit("power", {
        code,
        square: selected
    });
};

function parseFen(fen) {
    const [placement] = fen.split(" ");
    const rows = placement.split("/");
    const result = [];

    for (const row of rows) {
        const squares = [];

        for (const char of row) {
            if (/[1-8]/.test(char)) {
                for (let i = 0; i < Number(char); i++) {
                    squares.push(null);
                }
            } else {
                squares.push(char);
            }
        }

        result.push(squares);
    }

    return result;
}

function squareName(row, col) {
    return "abcdefgh"[col] + (8 - row);
}

function getPower(square) {
    return state?.pieces?.[square];
}

function render() {
    if (!state) return;

    const board = parseFen(state.fen);
    const element = $("board");

    element.innerHTML = "";

    const turnIsMine =
        (state.turn === "white" && myColor === "white") ||
        (state.turn === "black" && myColor === "black");

    for (let row = 0; row < 8; row++) {
        for (let col = 0; col < 8; col++) {

            const actualRow = myColor === "black" ? 7-row : row;
            const actualCol = myColor === "black" ? 7-col : col;

            const piece = board[actualRow][actualCol];
            const square = squareName(actualRow, actualCol);

            const button = document.createElement("button");
            button.className =
                "square " +
                ((actualRow + actualCol) % 2 ? "dark" : "light");

            if (selected === square) {
                button.classList.add("selected");
            }

            const selectedMoves = (state.legal || []).filter(
                move => move.slice(0, 2) === selected
            );

            const isTarget = selectedMoves.some(
                move => move.slice(2, 4) === square
            );

            if (isTarget) {
                button.classList.add("target");

                if (piece) {
                    button.classList.add("capture");
                }
            }

            if (piece) {
                const span = document.createElement("span");
                const isWhite = piece === piece.toUpperCase();

                span.className =
                    "piece " + (isWhite ? "white" : "black");

                span.textContent = GLYPHS[piece];

                const data = getPower(square);

                if (data) {
                    const base = document.createElement("span");
                    base.className = "power-base";
                    base.style.background = POWER[data.power][1];
                    base.style.boxShadow =
                        "0 0 12px " + POWER[data.power][1];

                    span.appendChild(base);

                    const badge = document.createElement("span");
                    badge.className = "power-symbol";
                    badge.style.background = POWER[data.power][1];
                    badge.textContent = POWER[data.power][0];

                    span.appendChild(badge);

                    if (data.shield) {
                        span.style.filter =
                            "drop-shadow(0 0 9px #459cff)";
                    }

                    if (data.aura) {
                        span.style.filter =
                            "drop-shadow(0 0 9px #40cfa5)";
                    }

                    if (data.used) {
                        span.style.opacity = ".65";
                    }
                }

                button.appendChild(span);
            }

            button.onclick = () => clickSquare(square, piece);

            element.appendChild(button);
        }
    }

    updatePanel(board);
}

function clickSquare(square, piece) {
    if (!state || state.status !== "playing") return;

    if (
        (state.turn === "white" && myColor !== "white") ||
        (state.turn === "black" && myColor !== "black")
    ) return;

    if (pendingPower) {
        socket.emit("special", {
            code,
            source: pendingPower,
            target: square
        });

        pendingPower = null;
        $("cancelPower").classList.add("hidden");
        return;
    }

    if (selected) {
        const move = (state.legal || []).find(
            m => m.slice(0, 2) === selected &&
                 m.slice(2, 4) === square
        );

        if (move) {
            socket.emit("move", { code, uci: move });
            selected = null;
            return;
        }
    }

    if (piece) {
        const isWhite = piece === piece.toUpperCase();
        const owner = isWhite ? "white" : "black";

        if (owner === myColor && owner === state.turn) {
            selected = square;
        } else {
            selected = null;
        }
    } else {
        selected = null;
    }

    render();
}

function updatePanel(board) {
    $("turn").textContent =
        state.turn === "white" ? "BIAŁE" : "CZARNE";

    const players = state.players || {};

    $("whitePlayer").textContent =
        "BIAŁE · " + (players.white?.name || "Oczekiwanie");

    $("blackPlayer").textContent =
        "CZARNE · " + (players.black?.name || "Oczekiwanie");

    if (state.check) {
        $("message").textContent = "SZACH!";
    } else if (state.status === "finished") {
        $("message").textContent =
            "KONIEC PARTII · " + state.result;
    } else {
        $("message").textContent =
            "Ruch: " + $("turn").textContent;
    }

    const data = selected ? getPower(selected) : null;

    $("pieceName").textContent = selected
        ? selected.toUpperCase()
        : "Nie wybrano";

    $("usePower").disabled =
        !data || data.used || !selected ||
        state.status !== "playing";

    if (data) {
        const [icon, color, title] = POWER[data.power];

        $("powerCard").innerHTML = `
            <span>${icon}</span>
            <div>
                <b style="color:${color}">${title}</b>
                <small>${data.used ? "Moc wykorzystana" :
                                    "Moc dostępna"}</small>
            </div>
        `;
    } else {
        $("powerCard").innerHTML = `
            <span>✨</span>
            <div>
                <b>SUPERMOCE</b>
                <small>Wybierz figurę na planszy.</small>
            </div>
        `;
    }

    const history = state.history || [];

    $("history").innerHTML = history.length
        ? history.slice().reverse().map(item =>
            `<div>${item.color === "white" ? "⚪" : "⚫"}
             ${item.move}</div>`
          ).join("")
        : "Brak ruchów.";
}

socket.on("state", data => {
    state = data;

    if (state.status === "waiting") {
        show("waiting");
        $("waitingMessage").textContent =
            "Oczekiwanie na drugiego gracza...";
        return;
    }

    show("game");
    render();
});
