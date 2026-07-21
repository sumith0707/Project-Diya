const socket = io();

socket.on("connect", () => {
    console.log("Connected");
});

function sendText() {
    const text = document.getElementById("txt").value;
    socket.emit("raw_text", text);
}

socket.on("reply", (msg) => {
    console.log(msg);
    document.getElementById("output").innerText = msg;
});

socket.on("sos", (msg) => {
    console.log(msg);
    document.getElementById("sos").innerText = msg;
});