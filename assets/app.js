// app.js – Merged version

const socket = io();

// ---- Connection ----
socket.on("connect", () => {
    console.log("Connected to server.");
});

socket.on("disconnect", () => {
    console.log("Disconnected.");
});

// ---- Raw Text ----
function sendText() {
    const text = document.getElementById("txt").value;
    if (text.trim()) {
        socket.emit("raw_text", text);
        document.getElementById("txt").value = "";
    }
}

socket.on("reply", (msg) => {
    document.getElementById("output").innerText = msg;
});

// ---- SOS ----
socket.on("sos", (msg) => {
    const el = document.getElementById("sos");
    el.innerText = "🚨 " + msg;
    el.style.display = "block";
    setTimeout(() => { el.style.display = "none"; }, 10000);
});

// ---- Video Feed ----
(function initVideo() {
    const iframe = document.getElementById('dynamicIframe');
    const placeholder = document.getElementById('videoPlaceholder');
    const currentHostname = window.location.hostname;
    const targetPort = 4912;
    const targetPath = '/embed';
    const streamUrl = `http://${currentHostname}:${targetPort}${targetPath}`;

    let intervalId;

    iframe.onload = () => {
        if (intervalId) clearInterval(intervalId);
        placeholder.style.display = 'none';
        iframe.style.display = 'block';
    };

    const startLoading = () => {
        iframe.src = streamUrl;
    };

    intervalId = setInterval(startLoading, 1000);
})();

// ---- Confidence Slider ----
const confidenceSlider = document.getElementById('confidenceSlider');
const confidenceInput = document.getElementById('confidenceInput');
const confidenceValueDisplay = document.getElementById('confidenceValueDisplay');
const sliderProgress = document.getElementById('sliderProgress');
const resetBtn = document.getElementById('confidenceResetButton');

function updateConfidenceDisplay() {
    const value = parseFloat(confidenceSlider.value);
    const percentage = ((value - confidenceSlider.min) / (confidenceSlider.max - confidenceSlider.min)) * 100;
    confidenceValueDisplay.textContent = value.toFixed(2);
    if (document.activeElement !== confidenceInput) {
        confidenceInput.value = value.toFixed(2);
    }
    sliderProgress.style.width = percentage + '%';
    confidenceValueDisplay.style.left = percentage + '%';
    socket.emit("override_th", value);
}

confidenceSlider.addEventListener('input', updateConfidenceDisplay);
confidenceInput.addEventListener('input', () => {
    let val = parseFloat(confidenceInput.value);
    if (isNaN(val)) val = 0.5;
    if (val < 0) val = 0;
    if (val > 1) val = 1;
    confidenceSlider.value = val;
    updateConfidenceDisplay();
});
resetBtn.addEventListener('click', () => {
    confidenceSlider.value = '0.5';
    confidenceInput.value = '0.50';
    updateConfidenceDisplay();
});
updateConfidenceDisplay();

// ---- Tracking Toggle ----
function toggleTracking(state) {
    socket.emit("toggle_tracking", state);
    document.getElementById("tracking_status").innerText = state === "on" ? "ON" : "OFF";
    document.getElementById("trackOnBtn").className = state === "on" ? "btn-on" : "btn-off";
    document.getElementById("trackOffBtn").className = state === "off" ? "btn-off" : "btn-on";
}

// ---- Detections ----
const MAX_RECENT_SCANS = 5;
let scans = [];

socket.on("detection", (message) => {
    console.log("Detection:", message);
    scans.unshift(message);
    if (scans.length > MAX_RECENT_SCANS) scans.pop();
    renderDetections();
    updateFeedback(message);
});

function renderDetections() {
    const el = document.getElementById('recentDetections');
    if (scans.length === 0) {
        el.innerHTML = `<li class="no-detections">No objects detected yet</li>`;
        return;
    }
    el.innerHTML = scans.map(scan => {
        const confidence = Math.floor((scan.confidence || 0) * 100);
        const label = scan.content || "unknown";
        const time = scan.timestamp ? new Date(scan.timestamp).toLocaleTimeString() : "now";
        return `<li><span><span class="detection-label">${label}</span> <span class="detection-conf">${confidence}%</span></span><span class="detection-time">${time}</span></li>`;
    }).join('');
}

function updateFeedback(detection) {
    const el = document.getElementById('feedback-content');
    if (detection && detection.content) {
        const label = detection.content;
        const confidence = Math.floor((detection.confidence || 0) * 100);
        const gifMap = {
            'cat': 'cat.webp',
            'cell phone': 'phone.webp',
            'clock': 'clock.webp',
            'cup': 'cup.webp',
            'dog': 'dog.webp',
            'potted plant': 'plant.webp'
        };
        const gif = gifMap[label] || null;
        if (gif) {
            el.innerHTML = `
                <div style="display:flex;flex-direction:column;align-items:center;">
                    <div style="font-size:28px;font-weight:bold;color:#f1c40f;">${confidence}%</div>
                    <img src="img/${gif}" alt="${label}" style="max-width:80px;max-height:80px;margin:5px 0;">
                    <p style="color:#2ecc71;font-weight:bold;">${label}</p>
                </div>
            `;
        } else {
            el.innerHTML = `
                <div style="display:flex;flex-direction:column;align-items:center;">
                    <div style="font-size:28px;font-weight:bold;color:#f1c40f;">${confidence}%</div>
                    <p style="color:#2ecc71;font-weight:bold;">${label}</p>
                    <p style="color:#888;font-size:12px;">No GIF for this object</p>
                </div>
            `;
        }
    } else {
        el.innerHTML = `
            <img src="img/stars.svg" alt="Stars" style="width:60px;height:60px;" />
            <p class="feedback-text">System response will appear here</p>
        `;
    }
}