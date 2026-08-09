// ============================================================
// Socket.IO Connection
// ============================================================
const socket = io();

socket.on("connect", () => {
    console.log("Connected to server.");
    document.getElementById('error-container').style.display = 'none';
});

socket.on("disconnect", () => {
    console.log("Disconnected from server.");
    document.getElementById('error-container').textContent = 'Connection lost. Please check the board.';
    document.getElementById('error-container').style.display = 'block';
});

// ============================================================
// Text Input Handler
// ============================================================
function sendText() {
    const text = document.getElementById("txt").value;
    if (text.trim()) {
        socket.emit("raw_text", text);
        document.getElementById("txt").value = '';
    }
}

socket.on("reply", (msg) => {
    console.log("Reply:", msg);
    document.getElementById("output").innerText = msg;
});

socket.on("sos", (msg) => {
    console.log("SOS:", msg);
    document.getElementById("sos").innerText = msg;
});

// ============================================================
// Object Detection Handlers
// ============================================================
const recentDetectionsElement = document.getElementById('recentDetections');
const feedbackContentElement = document.getElementById('feedback-content');
const MAX_RECENT_SCANS = 5;
let scans = [];

socket.on("detection", (entry) => {
    printDetection(entry);
    renderDetections();
    updateFeedback(entry);
});

// ============================================================
// Tracking Controls
// ============================================================
function toggleTracking(state) {
    socket.emit("toggle_tracking", state);
    const status = document.getElementById("tracking_status");
    status.innerText = state === "on" ? "ON" : "OFF";
    status.className = state === "on" ? "status-on" : "status-off";
}

// ============================================================
// Confidence Slider
// ============================================================
function initializeConfidenceSlider() {
    const slider = document.getElementById('confidenceSlider');
    const input = document.getElementById('confidenceInput');
    const resetBtn = document.getElementById('confidenceResetButton');

    slider.addEventListener('input', updateConfidenceDisplay);
    input.addEventListener('input', handleConfidenceInputChange);
    input.addEventListener('blur', validateConfidenceInput);
    resetBtn.addEventListener('click', resetConfidence);
}

function handleConfidenceInputChange() {
    const input = document.getElementById('confidenceInput');
    const slider = document.getElementById('confidenceSlider');
    let value = parseFloat(input.value);
    if (isNaN(value)) value = 0.5;
    if (value < 0) value = 0;
    if (value > 1) value = 1;
    slider.value = value;
    updateConfidenceDisplay();
}

function validateConfidenceInput() {
    const input = document.getElementById('confidenceInput');
    let value = parseFloat(input.value);
    if (isNaN(value)) value = 0.5;
    if (value < 0) value = 0;
    if (value > 1) value = 1;
    input.value = value.toFixed(2);
    handleConfidenceInputChange();
}

function updateConfidenceDisplay() {
    const slider = document.getElementById('confidenceSlider');
    const input = document.getElementById('confidenceInput');
    const display = document.getElementById('confidenceValueDisplay');
    const progress = document.getElementById('sliderProgress');

    const value = parseFloat(slider.value);
    socket.emit('override_th', value);

    const percentage = ((value - slider.min) / (slider.max - slider.min)) * 100;
    display.textContent = value.toFixed(2);
    if (document.activeElement !== input) {
        input.value = value.toFixed(2);
    }
    progress.style.width = percentage + '%';
    display.style.left = percentage + '%';
}

function resetConfidence() {
    const slider = document.getElementById('confidenceSlider');
    const input = document.getElementById('confidenceInput');
    slider.value = '0.5';
    input.value = '0.50';
    updateConfidenceDisplay();
}

// ============================================================
// Detection Display
// ============================================================
function printDetection(newDetection) {
    scans.unshift(newDetection);
    if (scans.length > MAX_RECENT_SCANS) {
        scans.pop();
    }
}

function renderDetections() {
    recentDetectionsElement.innerHTML = '';
    if (scans.length === 0) {
        recentDetectionsElement.innerHTML = '<li>No objects detected yet</li>';
        return;
    }
    scans.forEach(scan => {
        const li = document.createElement('li');
        const confidence = Math.floor(scan.confidence * 100);
        const time = new Date(scan.timestamp).toLocaleTimeString();
        li.innerHTML = `<span class="scan-content">${confidence}% - ${scan.content}</span>
                        <span class="scan-content-time">${time}</span>`;
        recentDetectionsElement.appendChild(li);
    });
}

function updateFeedback(detection) {
    if (detection) {
        const confidence = Math.floor(detection.confidence * 100);
        feedbackContentElement.innerHTML = `
            <div>
                <div style="font-size:24px;">${confidence}%</div>
                <div><strong>${detection.content}</strong></div>
                <div style="font-size:12px;color:#888;">Detected</div>
            </div>
        `;
    } else {
        feedbackContentElement.innerHTML = `
            <img src="img/stars.svg" alt="Stars" style="width:40px;opacity:0.5;" />
            <p style="color:#888;">System response will appear here</p>
        `;
    }
}

// ============================================================
// Initialize
// ============================================================
initializeConfidenceSlider();
renderDetections();

// Enter key for text input
document.getElementById('txt').addEventListener('keydown', function(e) {
    if (e.key === 'Enter') sendText();
});