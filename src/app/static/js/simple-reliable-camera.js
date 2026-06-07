/**
 * Simple Reliable Camera Client for Face Recognition Attendance System
 * A simplified version that focuses on reliable camera access
 */

class SimpleReliableCamera {
    constructor(options = {}) {
        // Configuration
        this.videoElement = options.videoElement;
        this.statusElement = options.statusElement;
        this.mode = options.mode || 'attendance';
        this.studentId = options.studentId;
        this.onStatusChange = options.onStatusChange || (() => {});
        this.onAttendanceMarked = options.onAttendanceMarked || (() => {});
        this.onRegistrationComplete = options.onRegistrationComplete || (() => {});
        this.onFaceDetected = options.onFaceDetected || (() => {});

        // State
        this.stream = null;
        this.status = 'disconnected';
        this.captureInterval = null;
        this.captureTimeout = null;
        this.processingImage = false;
        this.isCapturing = false;
        this.stopping = false;

        // Watchdog timer to detect and fix stalled face detection
        this.lastSuccessfulCapture = Date.now();
        this.watchdogTimer = null;
        this.watchdogInterval = 10000; // 10 seconds
        this.baseCaptureInterval = 180;
        this.minCaptureInterval = 120;
        this.maxCaptureInterval = 450;
        this.adaptiveCaptureInterval = this.baseCaptureInterval;
        this.targetProcessingWidth = options.targetProcessingWidth || 320;
        this.jpegQuality = options.jpegQuality || 0.65;

        // Smart Face Tracking for Intelligent Rate Limiting
        this.faceTracker = {
            lastFaces: [],
            lastProcessTime: 0,
            stableFaceCount: 0,
            lastSignificantChange: 0,
            processedFaceIds: new Set(),
            minProcessInterval: 2000, // Minimum 2 seconds between requests for same faces
            newFaceProcessInterval: 300, // Process new faces quickly (300ms)
            maxStableFrames: 8, // After 8 stable frames, reduce processing frequency
            faceChangeThreshold: 25, // Pixels movement to consider "significant change"
            attendanceMarkedFaces: new Set(), // Track faces that already marked attendance
            lastFaceHash: '', // Hash of face positions to detect changes
            consecutiveNoFaceFrames: 0, // Count frames with no faces
            resetAfterNoFaces: 5 // Reset tracking after N frames with no faces
        };

        this.deviceTier = this.detectDeviceTier();
        this.applyPerformanceProfile();

        // Canvas for capturing images
        this.canvas = document.createElement('canvas');
        this.canvas.width = 320;
        this.canvas.height = 240;
        this.ctx = this.canvas.getContext('2d');

        // Create overlay canvas for face detection visualization - simplified
        this.overlayCanvas = document.createElement('canvas');
        this.overlayCanvas.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            pointer-events: none;
            z-index: 1000;
        `;
        this.overlayCtx = this.overlayCanvas.getContext('2d');

        // Add resize observer to handle video element size changes
        this.resizeObserver = new ResizeObserver(() => {
            this.resizeOverlayCanvas();
        });

        // Bind methods
        this.start = this.start.bind(this);
        this.stop = this.stop.bind(this);
        this.captureAndSendImage = this.captureAndSendImage.bind(this);
        this.scheduleNextCapture = this.scheduleNextCapture.bind(this);
        this.updateStatus = this.updateStatus.bind(this);
        this.drawFaceDetectionBox = this.drawFaceDetectionBox.bind(this);
        this.shouldProcessFrame = this.shouldProcessFrame.bind(this);
        this.updateFaceTracker = this.updateFaceTracker.bind(this);
        this.calculateFaceDistance = this.calculateFaceDistance.bind(this);
        this.generateFaceHash = this.generateFaceHash.bind(this);
        this.detectDeviceTier = this.detectDeviceTier.bind(this);
        this.applyPerformanceProfile = this.applyPerformanceProfile.bind(this);
    }

    detectDeviceTier() {
        const cores = navigator.hardwareConcurrency || 4;
        const memory = navigator.deviceMemory || 4;
        const ua = navigator.userAgent || '';
        const isMobile = /Android|iPhone|iPad|iPod/i.test(ua);

        if (cores <= 4 || memory <= 4 || isMobile) {
            return 'low';
        }

        if (cores <= 8 || memory <= 8) {
            return 'mid';
        }

        return 'high';
    }

    applyPerformanceProfile() {
        if (this.deviceTier === 'low') {
            this.baseCaptureInterval = 260;
            this.minCaptureInterval = 180;
            this.maxCaptureInterval = 700;
            this.targetProcessingWidth = Math.min(this.targetProcessingWidth, 256);
            this.jpegQuality = Math.min(this.jpegQuality, 0.55);
            this.faceTracker.minProcessInterval = 2400;
            this.faceTracker.newFaceProcessInterval = 420;
            this.faceTracker.maxStableFrames = 5;
            return;
        }

        if (this.deviceTier === 'mid') {
            this.baseCaptureInterval = 200;
            this.minCaptureInterval = 140;
            this.maxCaptureInterval = 520;
            this.targetProcessingWidth = Math.min(this.targetProcessingWidth, 320);
            this.jpegQuality = Math.min(this.jpegQuality, 0.6);
            this.faceTracker.minProcessInterval = 2200;
            this.faceTracker.newFaceProcessInterval = 340;
            this.faceTracker.maxStableFrames = 6;
        }
    }

    /**
     * Smart face tracking to determine if we should process this frame
     * This prevents rate limiting by only processing when there are new faces or significant changes
     */
    shouldProcessFrame() {
        const now = Date.now();
        const tracker = this.faceTracker;

        // Always process the first frame
        if (tracker.lastProcessTime === 0) {
            console.log('🎯 First frame - processing');
            return true;
        }

        // If it's been too long since last process, force a check
        if (now - tracker.lastProcessTime > 10000) { // 10 seconds
            console.log('🕐 Timeout reached - forcing process');
            return true;
        }

        // For new faces, process more frequently
        if (now - tracker.lastSignificantChange < 5000) { // Within 5 seconds of change
            if (now - tracker.lastProcessTime >= tracker.newFaceProcessInterval) {
                console.log('🆕 New face detected recently - processing');
                return true;
            }
        }

        // For stable faces, process less frequently
        if (tracker.stableFaceCount >= tracker.maxStableFrames) {
            if (now - tracker.lastProcessTime >= tracker.minProcessInterval) {
                console.log('⏰ Stable face interval reached - processing');
                return true;
            }
        } else {
            // Still stabilizing, process at medium frequency
            if (now - tracker.lastProcessTime >= tracker.newFaceProcessInterval) {
                console.log('🔄 Stabilizing face - processing');
                return true;
            }
        }

        console.log('⏸️ Skipping frame - no significant change');
        return false;
    }

    /**
     * Update face tracker with current frame analysis
     */
    updateFaceTracker(faces = []) {
        const tracker = this.faceTracker;
        const now = Date.now();

        // If no faces detected
        if (!faces || faces.length === 0) {
            tracker.consecutiveNoFaceFrames++;

            // Reset tracking after several frames with no faces
            if (tracker.consecutiveNoFaceFrames >= tracker.resetAfterNoFaces) {
                console.log('🔄 Resetting face tracker - no faces detected');
                tracker.lastFaces = [];
                tracker.stableFaceCount = 0;
                tracker.processedFaceIds.clear();
                tracker.lastFaceHash = '';
                tracker.lastSignificantChange = now;
            }
            return;
        }

        // Reset no-face counter
        tracker.consecutiveNoFaceFrames = 0;

        // Generate hash of current face positions
        const currentHash = this.generateFaceHash(faces);

        // Check if faces have changed significantly
        const hasSignificantChange = currentHash !== tracker.lastFaceHash;

        if (hasSignificantChange) {
            console.log('📍 Significant face change detected');
            tracker.lastSignificantChange = now;
            tracker.stableFaceCount = 0;
            tracker.lastFaceHash = currentHash;

            // Check for new faces (faces not seen before)
            faces.forEach(face => {
                const faceId = this.generateFaceId(face);
                if (!tracker.processedFaceIds.has(faceId)) {
                    console.log('🆕 New face detected:', faceId);
                    tracker.processedFaceIds.add(faceId);
                }
            });
        } else {
            // Faces are stable, increment counter
            tracker.stableFaceCount++;
            console.log(`📊 Stable face count: ${tracker.stableFaceCount}/${tracker.maxStableFrames}`);
        }

        tracker.lastFaces = faces;
    }

    /**
     * Generate a simple hash of face positions for change detection
     */
    generateFaceHash(faces) {
        if (!faces || faces.length === 0) return '';

        return faces.map(face =>
            `${Math.round(face.x/10)}:${Math.round(face.y/10)}:${Math.round(face.width/10)}:${Math.round(face.height/10)}`
        ).sort().join('|');
    }

    /**
     * Generate a unique ID for a face based on position and size
     */
    generateFaceId(face) {
        return `face_${Math.round(face.x/20)}_${Math.round(face.y/20)}_${Math.round(face.width/20)}_${Math.round(face.height/20)}`;
    }

    /**
     * Calculate distance between two face positions
     */
    calculateFaceDistance(face1, face2) {
        const dx = face1.x - face2.x;
        const dy = face1.y - face2.y;
        const dw = face1.width - face2.width;
        const dh = face1.height - face2.height;
        return Math.sqrt(dx*dx + dy*dy + dw*dw + dh*dh);
    }

    /**
     * Start the camera client with multiple fallback methods
     */
    async start() {
        console.log('🎥 Starting SimpleReliableCamera');
        console.log('🔍 Video element:', this.videoElement);
        console.log('🔍 Mode:', this.mode);
        this.updateStatus('connecting', 'Connecting to camera...');

        // Set stopping flag to false
        this.stopping = false;

        try {
            // Check prerequisites
            if (!this.videoElement) {
                throw new Error('No video element provided to SimpleReliableCamera');
            }

            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                throw new Error('MediaDevices API not supported in this browser');
            }

            // Check secure context
            if (!window.isSecureContext && location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1' && location.hostname !== '0.0.0.0') {
                throw new Error('Camera access requires a secure context (HTTPS, localhost, or loopback)');
            }

            console.log('✅ Prerequisites check passed');

            // Try multiple camera access methods with different constraint sets
            let stream = null;
            let error = null;

            const prefersLowPowerProfile = this.deviceTier === 'low';
            const constraintSets = [
                // Method 1: Standard constraints optimized for face detection
                {
                    video: {
                        width: { ideal: prefersLowPowerProfile ? 480 : 640, min: 320 },
                        height: { ideal: prefersLowPowerProfile ? 360 : 480, min: 240 },
                        frameRate: prefersLowPowerProfile
                            ? { ideal: 8, max: 10, min: 6 }
                            : { ideal: 12, max: 15, min: 8 },
                        facingMode: 'user'
                    },
                    audio: false
                },
                // Method 2: Simpler constraints
                {
                    video: {
                        width: { ideal: prefersLowPowerProfile ? 256 : 320 },
                        height: { ideal: prefersLowPowerProfile ? 192 : 240 },
                        frameRate: prefersLowPowerProfile
                            ? { ideal: 7, max: 9 }
                            : { ideal: 10, max: 12 }
                    },
                    audio: false
                },
                // Method 3: Basic constraints
                {
                    video: true,
                    audio: false
                },
                // Method 4: Minimal constraints
                {
                    video: { facingMode: 'user' }
                }
            ];

            for (let i = 0; i < constraintSets.length; i++) {
                try {
                    console.log(`🎯 Trying constraint set ${i + 1}:`, constraintSets[i]);
                    stream = await navigator.mediaDevices.getUserMedia(constraintSets[i]);
                    console.log(`✅ Constraint set ${i + 1} succeeded`);
                    break;
                } catch (e) {
                    console.warn(`❌ Constraint set ${i + 1} failed:`, e);
                    error = e;

                    // If this is a permission error, don't try other constraints
                    if (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError') {
                        console.log('🚫 Permission denied, stopping constraint attempts');
                        break;
                    }
                }
            }

            // If all constraint sets failed, try legacy getUserMedia
            if (!stream) {
                console.log('🔄 Trying legacy getUserMedia as fallback');
                try {
                    const getUserMedia = navigator.getUserMedia ||
                                       navigator.webkitGetUserMedia ||
                                       navigator.mozGetUserMedia ||
                                       navigator.msGetUserMedia;

                    if (getUserMedia) {
                        stream = await new Promise((resolve, reject) => {
                            getUserMedia.call(navigator,
                                { video: true, audio: false },
                                resolve,
                                reject
                            );
                        });
                        console.log('✅ Legacy getUserMedia succeeded');
                    } else {
                        throw new Error('Legacy getUserMedia not available');
                    }
                } catch (e3) {
                    console.warn('❌ Legacy getUserMedia failed:', e3);
                    error = e3;
                }
            }

            // If all methods failed, throw the last error
            if (!stream) {
                throw error || new Error('All camera access methods failed');
            }

            console.log('🎉 Camera stream obtained successfully');
            console.log('📊 Stream info:', {
                active: stream.active,
                tracks: stream.getTracks().length,
                videoTracks: stream.getVideoTracks().length,
                deviceTier: this.deviceTier
            });

            // Store the stream
            this.stream = stream;

            // Set up video element
            console.log('🎬 Setting up video element...');
            this.videoElement.srcObject = stream;
            this.videoElement.muted = true;
            this.videoElement.setAttribute('playsinline', '');
            this.videoElement.setAttribute('autoplay', '');

            // Wait for video metadata to load
            await new Promise((resolve, reject) => {
                const timeout = setTimeout(() => {
                    reject(new Error('Video metadata load timeout'));
                }, 10000); // 10 second timeout

                this.videoElement.onloadedmetadata = () => {
                    clearTimeout(timeout);
                    console.log('📹 Video metadata loaded');
                    console.log('📐 Video dimensions:', {
                        videoWidth: this.videoElement.videoWidth,
                        videoHeight: this.videoElement.videoHeight,
                        clientWidth: this.videoElement.clientWidth,
                        clientHeight: this.videoElement.clientHeight
                    });
                    resolve();
                };

                this.videoElement.onerror = (error) => {
                    clearTimeout(timeout);
                    console.error('❌ Video error:', error);
                    reject(new Error('Video element error'));
                };
            });

            // Start video playback with retry logic
            let playAttempts = 0;
            const maxPlayAttempts = 3;

            while (playAttempts < maxPlayAttempts) {
                try {
                    playAttempts++;
                    console.log(`▶️ Video play attempt ${playAttempts}/${maxPlayAttempts}`);
                    await this.videoElement.play();
                    console.log('✅ Video playback started successfully');
                    break;
                } catch (playError) {
                    console.warn(`❌ Video play attempt ${playAttempts} failed:`, playError);

                    if (playAttempts < maxPlayAttempts) {
                        console.log('⏳ Waiting 1 second before retry...');
                        await new Promise(r => setTimeout(r, 1000));
                    } else {
                        throw new Error(`Video playback failed after ${maxPlayAttempts} attempts: ${playError.message}`);
                    }
                }
            }

            // Set canvas dimensions based on video
            const videoWidth = this.videoElement.videoWidth || 640;
            const videoHeight = this.videoElement.videoHeight || 480;
            const processingScale = Math.min(1, this.targetProcessingWidth / videoWidth);
            this.canvas.width = Math.max(160, Math.round(videoWidth * processingScale));
            this.canvas.height = Math.max(120, Math.round(videoHeight * processingScale));
            console.log('🎨 Processing canvas dimensions set:', {
                sourceWidth: videoWidth,
                sourceHeight: videoHeight,
                width: this.canvas.width,
                height: this.canvas.height
            });

            // Add overlay canvas
            if (this.videoElement.parentElement) {
                console.log('🎯 Setting up overlay canvas...');
                this.videoElement.parentElement.style.position = 'relative';

                // Set initial canvas dimensions
                this.resizeOverlayCanvas();

                // Add overlay canvas to video container if not already added
                if (!this.overlayCanvas.parentElement) {
                    this.videoElement.parentElement.appendChild(this.overlayCanvas);
                    console.log('✅ Overlay canvas added to container');
                }

                // Start observing video element size changes
                this.resizeObserver.observe(this.videoElement);
                console.log('👁️ Resize observer started');
            }

            this.updateStatus('connected', 'Connected to camera');

            // Start adaptive capture loop optimized for lower-end devices.
            this.isCapturing = true;
            this.scheduleNextCapture(0);
            console.log('📸 Adaptive image capture started');

            // Start watchdog timer to detect and fix stalled face detection
            this.lastSuccessfulCapture = Date.now();
            this.startWatchdogTimer();

            console.log('🎉 SimpleReliableCamera started successfully');
        } catch (error) {
            console.error('Error starting camera:', error);
            this.updateStatus('error', `Error: ${error.message}`);
            throw error;
        }
    }

    /**
     * Stop the camera client
     */
    stop() {
        console.log('Stopping SimpleReliableCamera');

        // Set stopping flag to prevent new captures
        this.stopping = true;
        this.isCapturing = false;

        // Clear capture interval
        if (this.captureInterval) {
            clearInterval(this.captureInterval);
            this.captureInterval = null;
        }
        if (this.captureTimeout) {
            clearTimeout(this.captureTimeout);
            this.captureTimeout = null;
        }

        // Clear watchdog timer
        this.stopWatchdogTimer();

        // Stop all tracks
        if (this.stream) {
            const tracks = this.stream.getTracks();
            tracks.forEach(track => {
                try {
                    track.stop();
                    console.log(`Stopped track: ${track.kind}`);
                } catch (e) {
                    console.warn(`Error stopping track: ${e.message}`);
                }
            });
            this.stream = null;
        }

        // Clear video element
        if (this.videoElement) {
            this.videoElement.srcObject = null;
            this.videoElement.pause();
        }

        // Remove overlay canvas
        if (this.overlayCanvas && this.overlayCanvas.parentElement) {
            this.overlayCanvas.parentElement.removeChild(this.overlayCanvas);
        }

        // Notify server to release resources
        fetch('/release_camera', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mode: this.mode,
                student_id: this.studentId
            })
        }).catch(err => console.warn('Error releasing resources:', err));

        this.updateStatus('disconnected', 'Disconnected');

        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
        }
    }

    scheduleNextCapture(delay = this.adaptiveCaptureInterval) {
        if (this.captureTimeout) {
            clearTimeout(this.captureTimeout);
        }
        if (!this.isCapturing || this.stopping) {
            return;
        }
        this.captureTimeout = setTimeout(() => {
            this.captureAndSendImage();
        }, delay);
    }

    resizeOverlayCanvas() {
        if (!this.videoElement || !this.overlayCanvas) return;

        // Get video container dimensions
        const container = this.videoElement.parentElement;
        if (!container) return;

        // Set overlay canvas to match video container size
        const rect = container.getBoundingClientRect();
        this.overlayCanvas.width = rect.width;
        this.overlayCanvas.height = rect.height;

        // Clear canvas
        this.overlayCtx.clearRect(0, 0, rect.width, rect.height);
    }

    /**
     * Draw face detection box on overlay canvas with liveness detection support
     */
    drawFaceDetectionBox(x, y, width, height, label = 'Face Detected', faceType = 'detected') {
        if (!this.overlayCanvas || !this.overlayCtx || !this.videoElement) return;

        // Get video dimensions
        const videoWidth = this.videoElement.videoWidth;
        const videoHeight = this.videoElement.videoHeight;
        if (!videoWidth || !videoHeight) {
            console.warn('Video dimensions not available');
            return;
        }

        // Get display dimensions
        const displayWidth = this.videoElement.clientWidth;
        const displayHeight = this.videoElement.clientHeight;

        // Calculate scale factors
        const scaleX = displayWidth / videoWidth;
        const scaleY = displayHeight / videoHeight;

        // Scale coordinates
        const boxX = x * scaleX;
        const boxY = y * scaleY;
        const boxWidth = width * scaleX;
        const boxHeight = height * scaleY;

        // Store the last face rect for reference
        this.lastFaceRect = { x, y, width, height };

        // Determine box color and style based on face type
        let strokeColor = '#00FF00'; // Default green
        let fillColor = '#00FF00';
        let lineWidth = 2;

        if (faceType === 'spoofing_detected' || label.includes('SPOOFING DETECTED')) {
            strokeColor = '#FF0000'; // Red for spoofing
            fillColor = '#FF0000';
            lineWidth = 3; // Thicker line for warnings
        } else if (faceType === 'attendance_marked') {
            strokeColor = '#00FF00'; // Green for successful attendance
            fillColor = '#00FF00';
        } else if (faceType === 'already_marked_today') {
            strokeColor = '#FFA500'; // Orange for already marked
            fillColor = '#FFA500';
        } else if (faceType === 'unknown_face_live') {
            strokeColor = '#FFFF00'; // Yellow for unknown but live
            fillColor = '#FFFF00';
        }

        // Draw box - don't clear previous drawings to support multiple faces
        this.overlayCtx.strokeStyle = strokeColor;
        this.overlayCtx.lineWidth = lineWidth;
        this.overlayCtx.strokeRect(boxX, boxY, boxWidth, boxHeight);

        // Draw label with background for better visibility
        this.overlayCtx.font = 'bold 12px Arial';
        const labelY = Math.max(boxY - 5, 15);

        // Measure text width for background
        const textMetrics = this.overlayCtx.measureText(label);
        const textWidth = textMetrics.width;

        // Draw background rectangle for label
        this.overlayCtx.fillStyle = 'rgba(0, 0, 0, 0.7)';
        this.overlayCtx.fillRect(boxX - 2, labelY - 14, textWidth + 4, 16);

        // Draw label text
        this.overlayCtx.fillStyle = fillColor;
        this.overlayCtx.fillText(label, boxX, labelY);
    }

    /**
     * Draw multiple face detection boxes with liveness detection support
     */
    drawMultipleFaceBoxes(faces) {
        if (!this.overlayCanvas || !this.overlayCtx) return;

        // Clear previous drawings
        this.overlayCtx.clearRect(0, 0, this.overlayCanvas.width, this.overlayCanvas.height);

        // Draw each face
        if (faces && Array.isArray(faces)) {
            faces.forEach(face => {
                const { x, y, width, height, label, type } = face;
                this.drawFaceDetectionBox(x, y, width, height, label || 'Face Detected', type || 'detected');
            });
        }
    }

    /**
     * Capture and send image to server
     */
    async captureAndSendImage() {
        // Skip if already processing, stopping, or video not ready
        if (this.processingImage || this.stopping || !this.videoElement || !this.stream || !this.isCapturing) {
            return;
        }

        if (this.mode === 'attendance' && !this.shouldProcessFrame()) {
            this.scheduleNextCapture(this.faceTracker.newFaceProcessInterval);
            return;
        }

        try {
            this.processingImage = true;
            const captureStart = performance.now();

            // Draw video frame to canvas
            this.ctx.drawImage(this.videoElement, 0, 0, this.canvas.width, this.canvas.height);

            // Convert canvas to blob
            const blob = await new Promise(resolve => {
                this.canvas.toBlob(resolve, 'image/jpeg', this.jpegQuality);
            });

            // Create form data
            const formData = new FormData();
            formData.append('image', blob, 'capture.jpg');
            formData.append('mode', this.mode);
            formData.append('student_id', this.studentId || '');

            // Show waiting status
            this.showStatusOverlay('🔄 Waiting for Face Detection');

            // Send to server
            const response = await fetch(`/process_image?t=${new Date().getTime()}`, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                throw new Error(`Server returned ${response.status}`);
            }

            // Parse response
            const result = await response.json();
            console.log('Face detection response:', result);

            // Update last successful capture time
            this.lastSuccessfulCapture = Date.now();
            const captureDuration = performance.now() - captureStart;
            this.adaptiveCaptureInterval = Math.max(
                this.minCaptureInterval,
                Math.min(this.maxCaptureInterval, Math.round(captureDuration * 0.35))
            );

            // Handle face detection
            if (result.faces_detected) {
                const detectedFaces = Array.isArray(result.faces)
                    ? result.faces
                    : (result.face_rect ? [result.face_rect] : []);
                this.updateFaceTracker(detectedFaces);
                this.faceTracker.lastProcessTime = Date.now();

                // Handle face detection boxes
                if (result.faces && Array.isArray(result.faces) && result.faces.length > 0) {
                    // Multiple faces case
                    console.log('Multiple faces detected:', result.faces.length);

                    // Call the global drawMultipleFaceResults function if available
                    if (typeof window.drawMultipleFaceResults === 'function') {
                        window.drawMultipleFaceResults(result.faces);
                    }
                    // Fallback to our local method
                    else {
                        this.drawMultipleFaceBoxes(result.faces);
                    }

                    this.onFaceDetected(result);
                }
                else if (result.face_rect) {
                    // Single face case (legacy support)
                    const { x, y, width, height } = result.face_rect;
                    console.log('Single face coordinates:', { x, y, width, height });

                    // Clear previous drawings first
                    this.overlayCtx.clearRect(0, 0, this.overlayCanvas.width, this.overlayCanvas.height);

                    // Draw the single face box
                    this.drawFaceDetectionBox(x, y, width, height);
                    this.onFaceDetected(result);
                }

                // Handle registration
                if (this.mode === 'registration' && result.capture_count !== undefined) {
                    this.captureCount = result.capture_count;

                    if (result.registration_complete) {
                        console.log('Registration complete! Stopping capture immediately.');
                        this.showStatusOverlay('✅ Registration Complete');
                        
                        // IMMEDIATELY stop the capture interval
                        if (this.captureInterval) {
                            clearInterval(this.captureInterval);
                            this.captureInterval = null;
                            console.log('Capture interval cleared');
                        }
                        
                        // Notify callback
                        this.onRegistrationComplete({ success: true });
                        
                        // Stop processing
                        this.stop();
                    }
                }
            } else {
                this.updateFaceTracker([]);
                this.faceTracker.lastProcessTime = Date.now();
                // Clear face detection box
                if (this.overlayCtx) {
                    this.overlayCtx.clearRect(0, 0, this.overlayCanvas.width, this.overlayCanvas.height);
                }

                // Show waiting status
                this.showStatusOverlay('🔄 Waiting for Face Detection');
            }

            // Handle attendance
            if (result.attendance_marked) {
                // Show success status
                this.showStatusOverlay('✅ Attendance Marked');

                this.onAttendanceMarked({
                    type: 'attendance_marked',
                    name: result.name,
                    student_id: result.student_id,
                    timestamp: result.timestamp
                });
            } else if (result.already_marked) {
                // Show already marked status
                this.showStatusOverlay('⚠️ Already Marked Recently');
            }
        } catch (error) {
            console.error('Error in captureAndSendImage:', error);

            // Clear face detection box
            if (this.overlayCtx) {
                this.overlayCtx.clearRect(0, 0, this.overlayCanvas.width, this.overlayCanvas.height);
            }
        } finally {
            this.processingImage = false;
            this.scheduleNextCapture();
        }
    }

    /**
     * Show status overlay with emoji
     */
    showStatusOverlay(message) {
        // Suppress overlay status in attendance mode; use header instead
        if (this.mode === 'attendance') return;
        if (!this.overlayCanvas || !this.overlayCtx) return;

        // Add status message at the top of the canvas - even smaller and more compact
        this.overlayCtx.font = 'bold 12px Arial';
        this.overlayCtx.fillStyle = 'rgba(0, 0, 0, 0.5)';
        this.overlayCtx.fillRect(0, 0, this.overlayCanvas.width, 20);
        this.overlayCtx.fillStyle = 'white';
        this.overlayCtx.textAlign = 'center';
        this.overlayCtx.fillText(message, this.overlayCanvas.width / 2, 14);
    }

    /**
     * Update status
     */
    updateStatus(status, message) {
        this.status = status;

        if (this.statusElement) {
            this.statusElement.textContent = message;
            this.statusElement.className = `status-${status}`;
        }

        this.onStatusChange(status, message);
    }

    /**
     * Start the watchdog timer to detect and fix stalled face detection
     */
    startWatchdogTimer() {
        // Clear any existing watchdog timer
        this.stopWatchdogTimer();

        // Start a new watchdog timer
        this.watchdogTimer = setInterval(() => {
            // Check if it's been too long since the last successful capture
            const now = Date.now();
            const timeSinceLastCapture = now - this.lastSuccessfulCapture;

            // If it's been more than the watchdog interval, restart the capture process
            if (timeSinceLastCapture > this.watchdogInterval && this.isCapturing && !this.stopping) {
                console.log(`Watchdog: Face detection appears stalled (${timeSinceLastCapture}ms). Restarting capture process...`);

                // Attempt to restart the capture process
                this.restartCaptureProcess();
            }
        }, 5000); // Check every 5 seconds

        console.log('Watchdog timer started');
    }

    /**
     * Stop the watchdog timer
     */
    stopWatchdogTimer() {
        if (this.watchdogTimer) {
            clearInterval(this.watchdogTimer);
            this.watchdogTimer = null;
            console.log('Watchdog timer stopped');
        }
    }

    /**
     * Restart the capture process if it appears to be stalled
     */
    async restartCaptureProcess() {
        // Only proceed if we're not already stopping or processing
        if (this.stopping || this.processingImage) {
            return;
        }

        console.log('Attempting to restart capture process...');

        try {
            // Clear existing capture interval
            if (this.captureInterval) {
                clearInterval(this.captureInterval);
                this.captureInterval = null;
            }
            if (this.captureTimeout) {
                clearTimeout(this.captureTimeout);
                this.captureTimeout = null;
            }

            // Reset processing flag
            this.processingImage = false;
            this.adaptiveCaptureInterval = this.baseCaptureInterval;

            // Restart the capture loop
            this.isCapturing = true;
            this.scheduleNextCapture(0);

            // Reset the last successful capture time
            this.lastSuccessfulCapture = Date.now();

            console.log('Capture process restarted successfully');
        } catch (error) {
            console.error('Error restarting capture process:', error);
        }
    }
}

// Export the class
window.SimpleReliableCamera = SimpleReliableCamera;
