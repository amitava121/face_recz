/**
 * Sleep Timer functionality for Face Recognition Attendance System
 * This script handles the automatic sleep mode functionality
 */

if (window.__sleepTimerScriptLoaded) {
    console.warn('sleep-timer.js already loaded, skipping duplicate initialization');
} else {
window.__sleepTimerScriptLoaded = true;

// Global variables
let sleepTimerSeconds = 0;
let sleepTimerInterval = null;
let lastActivityTime = Date.now();
let isProcessRunning = false;
let activeProcesses = new Set(); // Track active processes

// Helper to check if current page is a protected workstation
function isProtectedPath() {
    const path = window.location.pathname.toLowerCase();
    const protectedPaths = [
        '/register',
        '/capture_face',
        '/attendance',
        '/students',
        '/webrtc_test',
        '/webrtc_capture'
    ];
    return protectedPaths.some(p => path === p || path.startsWith(p + '/'));
}

// Helper to check if any video or MJPEG stream is active
function isCameraActive() {
    const videos = document.querySelectorAll('video');
    for (let video of videos) {
        if (video.srcObject && video.srcObject.active) {
            return true;
        }
        if (!video.paused && video.currentTime > 0) {
            return true;
        }
    }
    const images = document.querySelectorAll('img');
    for (let img of images) {
        if (img.src && (img.src.includes('/video_feed') || img.src.includes('video_feed'))) {
            return true;
        }
    }
    return false;
}

// Start the sleep timer
function startSleepTimer() {
    // Clear any existing interval
    if (sleepTimerInterval) {
        clearInterval(sleepTimerInterval);
    }

    // Set up the interval to check inactivity every second
    sleepTimerInterval = setInterval(checkInactivity, 1000);
    console.log('Sleep timer started, checking every second');
}

// Initialize the sleep timer
function initSleepTimer() {
    if (isProtectedPath()) {
        console.log('Workstation path detected. Sleep timer completely disabled.');
        return;
    }
    // Fetch the current sleep timer value from the server
    fetch('/get_sleep_timer')
        .then(response => response.json())
        .then(data => {
            sleepTimerSeconds = data.sleep_timer_seconds;
            console.log(`Sleep timer initialized: ${sleepTimerSeconds} seconds`);

            // Start the timer if it's set
            if (sleepTimerSeconds > 0) {
                startSleepTimer();
            }
        })
        .catch(error => {
            console.error('Error fetching sleep timer:', error);
        });
}

// Add a process to active processes
function addActiveProcess(processId) {
    activeProcesses.add(processId);
    setProcessRunning(true);
    console.log(`Process ${processId} added to active processes`);
}

// Remove a process from active processes
function removeActiveProcess(processId) {
    activeProcesses.delete(processId);
    if (activeProcesses.size === 0) {
        setProcessRunning(false);
    }
    console.log(`Process ${processId} removed from active processes`);
}

// Set process running state
function setProcessRunning(running) {
    isProcessRunning = running;
    if (running) {
        lastActivityTime = Date.now(); // Reset activity timer when process starts
        console.log('Process is running, sleep timer paused');
    } else {
        console.log('No active processes, sleep timer resumed');
    }
}

// Check if the system has been inactive for the sleep timer duration
function checkInactivity() {
    if (isProtectedPath() || isCameraActive()) {
        lastActivityTime = Date.now();
        return;
    }
    // If any process is running, don't go to sleep
    if (isProcessRunning || activeProcesses.size > 0) {
        console.log('Active process detected, resetting inactivity timer');
        lastActivityTime = Date.now();
        return;
    }

    // Calculate how long the system has been inactive
    const currentTime = Date.now();
    const inactiveTime = Math.floor((currentTime - lastActivityTime) / 1000);

    // If the inactive time exceeds the sleep timer, go to sleep
    if (inactiveTime >= sleepTimerSeconds && sleepTimerSeconds > 0) {
        console.log('Sleep timer triggered, going to sleep mode');
        clearInterval(sleepTimerInterval);
        window.location.href = '/sleep';
    }
}

// Reset the inactivity timer when there's user activity
function resetInactivityTimer() {
    lastActivityTime = Date.now();
}

// Add activity listeners
function addActivityListeners() {
    document.addEventListener('mousemove', resetInactivityTimer);
    document.addEventListener('mousedown', resetInactivityTimer);
    document.addEventListener('keypress', resetInactivityTimer);
    document.addEventListener('touchstart', resetInactivityTimer);
    document.addEventListener('scroll', resetInactivityTimer);

    // Add form interaction listeners
    document.addEventListener('focus', function(e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
            const processId = 'form-' + Date.now();
            addActiveProcess(processId);
        }
    }, true);

    document.addEventListener('blur', function(e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
            const processId = 'form-' + Date.now();
            removeActiveProcess(processId);
        }
    }, true);

    // Optimized form submission tracking (throttled)
    let formSubmissionTimeout;
    document.addEventListener('submit', function(e) {
        clearTimeout(formSubmissionTimeout);
        formSubmissionTimeout = setTimeout(() => {
            const processId = 'form-submit-' + Date.now();
            addActiveProcess(processId);
            setTimeout(() => removeActiveProcess(processId), 3000); // Reduced timeout
        }, 100);
    });

    // Throttled input tracking for better performance
    let inputTimeout;
    document.addEventListener('input', function(e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
            clearTimeout(inputTimeout);
            inputTimeout = setTimeout(() => resetInactivityTimer(), 200);
        }
    });

    // Listen for WebRTC events
    document.addEventListener('webrtc:start', (e) => {
        const processId = e.detail?.processId || 'webrtc-' + Date.now();
        addActiveProcess(processId);
    });

    document.addEventListener('webrtc:stop', (e) => {
        const processId = e.detail?.processId || 'webrtc-' + Date.now();
        removeActiveProcess(processId);
    });

    // Listen for registration events
    document.addEventListener('registration:start', (e) => {
        const processId = e.detail?.processId || 'registration-' + Date.now();
        addActiveProcess(processId);
    });

    document.addEventListener('registration:complete', (e) => {
        const processId = e.detail?.processId || 'registration-' + Date.now();
        removeActiveProcess(processId);
    });

    // Listen for attendance events
    document.addEventListener('attendance:start', (e) => {
        const processId = e.detail?.processId || 'attendance-' + Date.now();
        addActiveProcess(processId);
    });

    document.addEventListener('attendance:stop', (e) => {
        const processId = e.detail?.processId || 'attendance-' + Date.now();
        removeActiveProcess(processId);
    });
}

// Initialize when the DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Initialize sleep timer
    initSleepTimer();

    // Add activity listeners
    addActivityListeners();

    // Check for attendance or registration processes
    const attendanceElements = document.querySelectorAll('.video-container, #videoContainer');
    if (attendanceElements.length > 0) {
        console.log('Attendance or registration process detected');
        const processId = 'video-' + Date.now();
        addActiveProcess(processId);
    }

    // Add event listeners for process start/stop buttons if they exist
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');

    if (startBtn) {
        startBtn.addEventListener('click', function() {
            const processId = 'button-' + Date.now();
            addActiveProcess(processId);
        });
    }

    if (stopBtn) {
        stopBtn.addEventListener('click', function() {
            const processId = 'button-' + Date.now();
            removeActiveProcess(processId);
        });
    }

    // Add event listener for sleep timer form submission
    const sleepTimerForm = document.querySelector('form[action*="set_sleep_timer"]');
    if (sleepTimerForm) {
        sleepTimerForm.addEventListener('submit', function() {
            // Wait for the server to update the timer value
            setTimeout(() => {
                initSleepTimer();  // Reinitialize the timer with new value
            }, 500);
        });
    }
});
}
