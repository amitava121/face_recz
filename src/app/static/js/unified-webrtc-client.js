/**
 * Unified WebRTC Client for Face Recognition Attendance System
 * Used for both face registration and attendance marking
 */

class UnifiedWebRTCClient {
    constructor(options = {}) {
        // Configuration
        this.webrtcUrl = options.webrtcUrl || window.location.origin.replace(/^http/, 'ws') + '/webrtc';
        this.videoElement = options.videoElement;
        this.statusElement = options.statusElement;
        this.mode = options.mode || 'attendance'; // 'attendance' or 'registration'
        this.studentId = options.studentId;
        this.onStatusChange = options.onStatusChange || (() => {});
        this.onAttendanceMarked = options.onAttendanceMarked || (() => {});
        this.onRegistrationComplete = options.onRegistrationComplete || (() => {});
        this.onFaceDetected = options.onFaceDetected || (() => {});

        // State
        this.pc = null;
        this.localStream = null;
        this.status = 'disconnected';
        this.isProcessing = false;
        this.captureCount = 0; // Track the number of face captures

        // Bind methods
        this.start = this.start.bind(this);
        this.stop = this.stop.bind(this);
        this.createPeerConnection = this.createPeerConnection.bind(this);
        this.negotiate = this.negotiate.bind(this);
        this.updateStatus = this.updateStatus.bind(this);

        // Initialize
        this.initialize();
    }

    /**
     * Initialize the WebRTC client
     */
    initialize() {
        console.log('Initializing WebRTC client...');
        console.log('Browser:', navigator.userAgent);

        // More permissive browser detection for modern browsers
        const isChrome = /Chrome/.test(navigator.userAgent) && /Google Inc/.test(navigator.vendor);
        const isSafari = /Safari/.test(navigator.userAgent) && /Apple Computer/.test(navigator.vendor);
        const isFirefox = /Firefox/.test(navigator.userAgent);
        const isEdge = /Edg/.test(navigator.userAgent);

        console.log('Browser detection:', { isChrome, isSafari, isFirefox, isEdge });

        // For known modern browsers, we'll be more permissive
        const isModernBrowser = isChrome || isSafari || isFirefox || isEdge;

        // Check if WebRTC is supported
        if (!navigator.mediaDevices) {
            console.error('MediaDevices API not supported in this browser');

            // For modern browsers, try to work around the issue
            if (isModernBrowser) {
                console.warn('Modern browser detected but MediaDevices API not found. Attempting to continue anyway.');
                // Create a placeholder if needed
                if (!window.navigator.mediaDevices) {
                    window.navigator.mediaDevices = {};
                }

                // Polyfill getUserMedia
                if (!navigator.mediaDevices.getUserMedia) {
                    navigator.mediaDevices.getUserMedia = function(constraints) {
                        console.warn('Using polyfill for getUserMedia');
                        const getUserMedia = navigator.webkitGetUserMedia || navigator.mozGetUserMedia;
                        if (!getUserMedia) {
                            this.updateStatus('error', 'Your browser does not support camera access. Please try a different browser like Chrome or Firefox.');
                            return Promise.reject(new Error('getUserMedia is not implemented in this browser'));
                        }
                        return new Promise(function(resolve, reject) {
                            getUserMedia.call(navigator, constraints, resolve, reject);
                        });
                    };
                }
            } else {
                this.updateStatus('error', 'Your browser does not support camera access. Please try a different browser like Chrome or Firefox.');
                return;
            }
        }

        // Check for RTCPeerConnection with polyfill for older browsers
        if (!window.RTCPeerConnection) {
            console.error('RTCPeerConnection not supported in this browser');

            // Try polyfills for known browsers
            window.RTCPeerConnection = window.RTCPeerConnection ||
                                      window.webkitRTCPeerConnection ||
                                      window.mozRTCPeerConnection;

            if (!window.RTCPeerConnection) {
                this.updateStatus('error', 'Your browser does not support WebRTC. Please try a different browser like Chrome or Firefox.');
                return;
            } else {
                console.warn('Using polyfill for RTCPeerConnection');
            }
        }

        // Add event listeners
        window.addEventListener('beforeunload', () => {
            this.stop();
        });

        console.log('WebRTC client initialized successfully');
    }

    /**
     * Check if camera permissions are available
     */
    async checkCameraPermission() {
        try {
            console.log('Checking camera permissions...');

            // More permissive browser detection for modern browsers
            const isChrome = /Chrome/.test(navigator.userAgent) && /Google Inc/.test(navigator.vendor);
            const isSafari = /Safari/.test(navigator.userAgent) && /Apple Computer/.test(navigator.vendor);
            const isFirefox = /Firefox/.test(navigator.userAgent);
            const isEdge = /Edg/.test(navigator.userAgent);
            const isModernBrowser = isChrome || isSafari || isFirefox || isEdge;

            // First check if mediaDevices is supported
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                console.error('MediaDevices API not supported in this browser');

                // For modern browsers, try to work around the issue
                if (isModernBrowser) {
                    console.warn('Modern browser detected but MediaDevices API not found. Attempting to continue anyway.');

                    // Create a placeholder if needed
                    if (!window.navigator.mediaDevices) {
                        window.navigator.mediaDevices = {};
                    }

                    // Polyfill getUserMedia
                    if (!navigator.mediaDevices.getUserMedia) {
                        const getUserMedia = navigator.webkitGetUserMedia || navigator.mozGetUserMedia;
                        if (!getUserMedia) {
                            return {
                                granted: false,
                                error: 'Your browser does not support camera access. Please try a different browser like Chrome or Firefox.',
                                errorName: 'MediaDevicesNotSupported'
                            };
                        }

                        navigator.mediaDevices.getUserMedia = function(constraints) {
                            console.warn('Using polyfill for getUserMedia');
                            return new Promise(function(resolve, reject) {
                                getUserMedia.call(navigator, constraints, resolve, reject);
                            });
                        };
                    }
                } else {
                    return {
                        granted: false,
                        error: 'Your browser does not support camera access. Please try a different browser like Chrome or Firefox.',
                        errorName: 'MediaDevicesNotSupported'
                    };
                }
            }

            // Check if we're in a secure context (HTTPS or localhost)
            if (window.location.protocol !== 'https:' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1' && window.location.hostname !== '0.0.0.0') {
                console.error('Not in a secure context, camera access may be blocked');
                console.log('Current location:', window.location.protocol, window.location.hostname);

                // For local development, we'll be more permissive
                if (window.location.hostname.startsWith('192.168.') ||
                    window.location.hostname.startsWith('10.') ||
                    window.location.hostname.includes('.local')) {
                    console.warn('Local network detected, continuing despite insecure context');
                } else {
                    return {
                        granted: false,
                        error: 'Camera access requires a secure connection (HTTPS). Please use HTTPS or localhost.',
                        errorName: 'InsecureContext'
                    };
                }
            }

            // Try to get camera permission with a timeout
            const permissionPromise = navigator.mediaDevices.getUserMedia({ video: true });
            const timeoutPromise = new Promise((_, reject) => {
                setTimeout(() => reject(new Error('Camera permission request timed out')), 10000);
            });

            const stream = await Promise.race([permissionPromise, timeoutPromise]);

            // If we get here, permission was granted
            // Stop all tracks to release the camera
            stream.getTracks().forEach(track => track.stop());

            return { granted: true };
        } catch (error) {
            console.error('Camera permission check failed:', error);

            let errorMessage = 'Could not access camera. ';
            let errorDetails = '';

            if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                errorMessage += 'Camera access was denied.';
                errorDetails = 'Please allow camera access in your browser settings and reload the page.';
            } else if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
                errorMessage += 'No camera was found on your device.';
                errorDetails = 'Please connect a camera and reload the page.';
            } else if (error.name === 'NotReadableError' || error.name === 'TrackStartError') {
                errorMessage += 'Your camera may be in use by another application.';
                errorDetails = 'Please close other applications that might be using your camera and reload the page.';
            } else if (error.name === 'AbortError') {
                errorMessage += 'Camera access request was aborted.';
                errorDetails = 'Please try again or use a different browser.';
            } else if (error.name === 'SecurityError') {
                errorMessage += 'Camera access was blocked due to security restrictions.';
                errorDetails = 'Please use a secure connection (HTTPS) or try a different browser.';
            } else if (error.name === 'TypeError') {
                errorMessage += 'Invalid constraints were used to access the camera.';
                errorDetails = 'This is a technical issue. Please try a different browser.';
            } else if (error.message && error.message.includes('timed out')) {
                errorMessage += 'Camera permission request timed out.';
                errorDetails = 'Please check your browser settings and try again.';
            } else {
                errorMessage += 'An unknown error occurred.';
                errorDetails = 'Please try a different browser like Chrome or Firefox.';
            }

            return {
                granted: false,
                error: errorMessage,
                errorDetails: errorDetails,
                errorName: error.name || 'UnknownError'
            };
        }
    }

    /**
     * Start the WebRTC connection
     */
    async start() {
        try {
            this.updateStatus('starting');
            
            // Dispatch start event for sleep timer
            document.dispatchEvent(new CustomEvent('webrtc:start', {
                detail: { processId: this.mode + '-' + this.studentId }
            }));

            // Make sure any previous connection is fully closed
            await this.stop();

            // Wait a moment for camera resources to be released
            await new Promise(resolve => setTimeout(resolve, 500));

            // Check camera permissions first
            const permissionCheck = await this.checkCameraPermission();
            if (!permissionCheck.granted) {
                const error = new Error(permissionCheck.error);
                error.errorDetails = permissionCheck.errorDetails;
                error.errorName = permissionCheck.errorName;
                throw error;
            }

            this.updateStatus('connecting', 'Connecting to camera...');

            // Get user media with fallback options
            try {
                // Make sure mediaDevices is available
                if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                    throw new Error('MediaDevices API not supported in this browser');
                }

                console.log('Requesting camera access with high FPS settings...');
                this.localStream = await navigator.mediaDevices.getUserMedia({
                    audio: false,
                    video: {
                        width: { ideal: 480, max: 640 },
                        height: { ideal: 360, max: 480 },
                        frameRate: { ideal: 30, max: 60 },
                        facingMode: "user"
                    }
                });
                console.log('Successfully accessed camera with optimized settings');
            } catch (mediaError) {
                console.warn('Failed to get ideal camera settings, trying fallback:', mediaError);

                // Try with simpler constraints
                try {
                    console.log('Requesting camera access with basic settings...');
                    this.localStream = await navigator.mediaDevices.getUserMedia({
                        audio: false,
                        video: true
                    });
                    console.log('Successfully accessed camera with basic settings');
                } catch (fallbackError) {
                    console.error('Failed to access camera with fallback settings:', fallbackError);

                    // Try one more time with a delay
                    await new Promise(resolve => setTimeout(resolve, 1000));

                    try {
                        console.log('Final attempt to access camera...');
                        this.localStream = await navigator.mediaDevices.getUserMedia({
                            audio: false,
                            video: {
                                facingMode: "user",
                                frameRate: { min: 15, ideal: 30 }
                            }
                        });
                        console.log('Successfully accessed camera on final attempt');
                    } catch (finalError) {
                        console.error('All camera access attempts failed:', finalError);

                        let errorMessage = 'Could not access camera. ';
                        if (finalError.name === 'NotAllowedError' || finalError.name === 'PermissionDeniedError') {
                            errorMessage += 'Camera access was denied. Please allow camera access in your browser settings.';
                        } else if (finalError.name === 'NotFoundError' || finalError.name === 'DevicesNotFoundError') {
                            errorMessage += 'No camera was found on your device.';
                        } else if (finalError.name === 'NotReadableError' || finalError.name === 'TrackStartError') {
                            errorMessage += 'Your camera may be in use by another application.';
                        } else {
                            errorMessage += 'Please check camera permissions and try again.';
                        }

                        throw new Error(errorMessage);
                    }
                }
            }

            // Display the local stream in the video element
            if (this.videoElement && this.localStream) {
                this.videoElement.srcObject = this.localStream;
                console.log('Local stream connected to video element');
            } else if (!this.videoElement) {
                console.error('No video element available to display stream');
            }

            // Create peer connection
            this.createPeerConnection();
            console.log('Peer connection created');

            // Add local stream to peer connection
            if (this.localStream) {
                const tracks = this.localStream.getTracks();
                console.log(`Adding ${tracks.length} tracks to peer connection`);

                tracks.forEach(track => {
                    try {
                        this.pc.addTrack(track, this.localStream);
                        console.log(`Added track: ${track.kind}`);
                    } catch (e) {
                        console.error('Error adding track to peer connection:', e);
                    }
                });
            } else {
                console.error('No local stream available to add tracks');
                throw new Error('No camera stream available');
            }

            // Start negotiation with timeout
            console.log('Starting WebRTC negotiation...');
            const negotiationPromise = this.negotiate();
            const timeoutPromise = new Promise((_, reject) => {
                setTimeout(() => reject(new Error('Connection timed out')), 15000);
            });

            await Promise.race([negotiationPromise, timeoutPromise]);
            console.log('Negotiation completed successfully');

            this.updateStatus('connected', 'Connected to WebRTC server');
        } catch (error) {
            console.error('Error starting WebRTC:', error);
            this.updateStatus('error');
            
            // Dispatch stop event for sleep timer on error
            document.dispatchEvent(new CustomEvent('webrtc:stop', {
                detail: { processId: this.mode + '-' + this.studentId }
            }));

            // Show a more user-friendly error message on the page
            if (this.videoElement) {
                // Create an error overlay
                const errorOverlay = document.createElement('div');
                errorOverlay.style.position = 'absolute';
                errorOverlay.style.top = '0';
                errorOverlay.style.left = '0';
                errorOverlay.style.width = '100%';
                errorOverlay.style.height = '100%';
                errorOverlay.style.backgroundColor = 'rgba(0, 0, 0, 0.7)';
                errorOverlay.style.color = 'white';
                errorOverlay.style.display = 'flex';
                errorOverlay.style.flexDirection = 'column';
                errorOverlay.style.justifyContent = 'center';
                errorOverlay.style.alignItems = 'center';
                errorOverlay.style.padding = '20px';
                errorOverlay.style.textAlign = 'center';
                errorOverlay.style.zIndex = '1000';

                const errorIcon = document.createElement('div');
                errorIcon.innerHTML = '❌';
                errorIcon.style.fontSize = '48px';
                errorIcon.style.marginBottom = '10px';

                const errorText = document.createElement('div');
                errorText.textContent = error.message;
                errorText.style.fontSize = '16px';
                errorText.style.lineHeight = '1.4';
                errorText.style.marginBottom = '10px';

                // Add error details if available
                let errorDetails = '';
                if (error.errorDetails) {
                    errorDetails = error.errorDetails;
                } else if (error.originalError && error.originalError.errorDetails) {
                    errorDetails = error.originalError.errorDetails;
                }

                if (errorDetails) {
                    const detailsText = document.createElement('div');
                    detailsText.textContent = errorDetails;
                    detailsText.style.fontSize = '14px';
                    detailsText.style.lineHeight = '1.4';
                    detailsText.style.opacity = '0.8';
                    detailsText.style.marginTop = '10px';
                    errorOverlay.appendChild(detailsText);
                }

                // Add browser suggestion
                const browserSuggestion = document.createElement('div');
                browserSuggestion.innerHTML = 'Please try using Chrome, Firefox, Edge, or Safari.';
                browserSuggestion.style.fontSize = '14px';
                browserSuggestion.style.marginTop = '15px';
                browserSuggestion.style.opacity = '0.9';

                errorOverlay.appendChild(errorIcon);
                errorOverlay.appendChild(errorText);
                errorOverlay.appendChild(browserSuggestion);

                // Add the overlay to the video container
                const videoContainer = this.videoElement.parentElement;
                if (videoContainer) {
                    videoContainer.style.position = 'relative';
                    videoContainer.appendChild(errorOverlay);
                }
            }
        }
    }

    /**
     * Stop the WebRTC connection and release resources
     */
    async stop() {
        try {
            this.updateStatus('stopping');
            
            // Dispatch stop event for sleep timer
            document.dispatchEvent(new CustomEvent('webrtc:stop', {
                detail: { processId: this.mode + '-' + this.studentId }
            }));

            if (this.status === 'disconnected') return;

            console.log('Stopping WebRTC connection...');

            // Update status first to provide immediate feedback
            this.updateStatus('disconnected', 'Disconnected');

            // Stop all tracks in the local stream
            if (this.localStream) {
                try {
                    const tracks = this.localStream.getTracks();
                    console.log(`Stopping ${tracks.length} tracks`);

                    tracks.forEach(track => {
                        try {
                            track.stop();
                            console.log(`Stopped track: ${track.kind}`);
                        } catch (trackError) {
                            console.warn(`Error stopping track: ${trackError.message}`);
                        }
                    });
                } catch (streamError) {
                    console.warn(`Error stopping stream: ${streamError.message}`);
                }
                this.localStream = null;
            }

            // Close peer connection
            if (this.pc) {
                try {
                    this.pc.close();
                    console.log('Closed peer connection');
                } catch (pcError) {
                    console.warn(`Error closing peer connection: ${pcError.message}`);
                }
                this.pc = null;
            }

            // Clear video element
            if (this.videoElement) {
                try {
                    this.videoElement.srcObject = null;
                    console.log('Cleared video element');
                } catch (videoError) {
                    console.warn(`Error clearing video element: ${videoError.message}`);
                }
            }

            // Notify server to release resources with timeout
            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 5000);

                fetch('/release_webrtc', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Cache-Control': 'no-cache'
                    },
                    body: JSON.stringify({
                        mode: this.mode,
                        student_id: this.studentId,
                        timestamp: new Date().getTime() // Add timestamp to prevent caching
                    }),
                    signal: controller.signal
                }).then(response => {
                    clearTimeout(timeoutId);
                    if (response.ok) {
                        console.log('Server resources released successfully');
                    } else {
                        console.warn(`Server returned status ${response.status} when releasing resources`);
                    }
                }).catch(err => {
                    clearTimeout(timeoutId);
                    console.error('Error releasing WebRTC resources:', err);
                });
            } catch (fetchError) {
                console.warn(`Error setting up fetch: ${fetchError.message}`);
            }
        } catch (error) {
            console.error('Error stopping WebRTC:', error);
        } finally {
            this.updateStatus('disconnected');
        }
    }

    /**
     * Create a new RTCPeerConnection
     */
    createPeerConnection() {
        const config = {
            iceServers: [
                { urls: 'stun:stun.l.google.com:19302' },
                { urls: 'stun:stun1.l.google.com:19302' },
                { urls: 'stun:stun2.l.google.com:19302' },
                { urls: 'stun:stun3.l.google.com:19302' },
                { urls: 'stun:stun4.l.google.com:19302' }
            ],
            iceCandidatePoolSize: 10,
            bundlePolicy: 'max-bundle',
            rtcpMuxPolicy: 'require'
        };

        this.pc = new RTCPeerConnection(config);

        // Handle ICE connection state change
        this.pc.addEventListener('iceconnectionstatechange', () => {
            console.log('ICE connection state:', this.pc.iceConnectionState);

            if (this.pc.iceConnectionState === 'failed' ||
                this.pc.iceConnectionState === 'closed') {
                this.stop();
            }
        });

        // Handle track from server
        this.pc.addEventListener('track', (event) => {
            if (this.videoElement && event.track.kind === 'video') {
                this.videoElement.srcObject = event.streams[0];

                // Set up canvas for processing video frames
                if (this.mode === 'registration') {
                    this.setupRegistrationDetection();
                }
            }
        });

        // Handle data channel for messages from server
        this.pc.addEventListener('datachannel', (event) => {
            const channel = event.channel;

            channel.addEventListener('message', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    console.log('Received data from server:', data);

                    if (data.type === 'face_detected') {
                        console.log('Face detected:', data);

                        // Add capture progress information if available
                        if (this.mode === 'registration' && data.hasOwnProperty('capture_count')) {
                            data.type = 'capture_progress';
                            data.count = data.capture_count;
                            // Update the internal capture count
                            this.captureCount = data.capture_count;
                            console.log('Capture progress updated:', data);
                        }

                        // Call the face detected callback with the data
                        this.onFaceDetected(data);

                        // Add visual indicator for face detection
                        const videoContainer = this.videoElement.parentElement;
                        if (videoContainer) {
                            // Check if we already have a face detection indicator
                            let indicator = videoContainer.querySelector('.face-detection-indicator');
                            if (!indicator) {
                                // Create indicator if it doesn't exist
                                indicator = document.createElement('div');
                                indicator.className = 'face-detection-indicator';
                                indicator.style.position = 'absolute';
                                indicator.style.top = '10px';
                                indicator.style.right = '10px';
                                indicator.style.backgroundColor = 'rgba(0, 255, 0, 0.7)';
                                indicator.style.color = 'white';
                                indicator.style.padding = '5px 10px';
                                indicator.style.borderRadius = '5px';
                                indicator.style.fontWeight = 'bold';
                                indicator.style.fontSize = '14px';
                                indicator.style.zIndex = '100';
                                indicator.style.transition = 'opacity 0.3s ease';
                                videoContainer.appendChild(indicator);
                            }

                            // Update indicator
                            indicator.textContent = `Faces: ${data.count}`;
                            if (this.mode === 'registration' && data.hasOwnProperty('capture_count')) {
                                indicator.textContent = `Faces: ${data.count} | Captures: ${data.capture_count}/5`;
                            }
                            indicator.style.opacity = '1';

                            // Hide indicator after 2 seconds
                            setTimeout(() => {
                                indicator.style.opacity = '0';
                            }, 2000);
                        }
                    } else if (data.type === 'attendance_marked') {
                        console.log('Attendance marked:', data);
                        this.onAttendanceMarked(data);

                        // Show attendance marked overlay
                        const videoContainer = this.videoElement.parentElement;
                        if (videoContainer) {
                            // Check if we have an attendance overlay
                            let overlay = document.getElementById('attendanceOverlay');
                            let nameOverlay = document.getElementById('studentNameOverlay');

                            if (overlay && nameOverlay) {
                                // Update and show the existing overlay
                                nameOverlay.textContent = data.name || 'Student';
                                overlay.style.display = 'flex';

                                // Hide overlay after 1.5 seconds
                                setTimeout(() => {
                                    overlay.style.display = 'none';
                                }, 1500);
                            }
                        }
                    } else if (data.type === 'registration_complete') {
                        console.log('Registration complete:', data);

                        // Check if we've already initiated a redirect
                        if (window.registrationRedirectInitiated) {
                            console.log('Redirect already initiated, ignoring duplicate message');
                            return;
                        }

                        // Set a flag to prevent multiple redirects
                        window.registrationRedirectInitiated = true;
                        console.log('Setting registrationRedirectInitiated flag to true');

                        // Show success message in the center of the screen
                        const successMessage = document.createElement('div');
                        successMessage.className = 'alert alert-success registration-complete-message';
                        successMessage.style.position = 'fixed';
                        successMessage.style.top = '50%';
                        successMessage.style.left = '50%';
                        successMessage.style.transform = 'translate(-50%, -50%)';
                        successMessage.style.zIndex = '9999';
                        successMessage.style.padding = '20px';
                        successMessage.style.borderRadius = '10px';
                        successMessage.style.boxShadow = '0 0 20px rgba(0,0,0,0.5)';
                        successMessage.style.backgroundColor = '#dff0d8';
                        successMessage.style.color = '#3c763d';
                        successMessage.style.fontSize = '18px';
                        successMessage.style.textAlign = 'center';
                        successMessage.innerHTML = '<strong>Success!</strong><br>Face registration complete.<br>Redirecting...';
                        document.body.appendChild(successMessage);

                        // Call the registration complete callback
                        try {
                            this.onRegistrationComplete(data);
                        } catch (callbackError) {
                            console.error('Error in registration complete callback:', callbackError);
                        }

                        // Close the connection
                        try {
                            console.log('Closing connection after registration complete');

                            // Stop all tracks
                            if (this.localStream) {
                                this.localStream.getTracks().forEach(track => track.stop());
                            }

                            // Close peer connection
                            if (this.pc) {
                                this.pc.close();
                            }

                            // Clear video element
                            if (this.videoElement) {
                                this.videoElement.srcObject = null;

                                // Add a success overlay to the video element
                                const videoContainer = this.videoElement.parentElement;
                                if (videoContainer) {
                                    const overlay = document.createElement('div');
                                    overlay.style.position = 'absolute';
                                    overlay.style.top = '0';
                                    overlay.style.left = '0';
                                    overlay.style.width = '100%';
                                    overlay.style.height = '100%';
                                    overlay.style.backgroundColor = 'rgba(0, 128, 0, 0.7)';
                                    overlay.style.display = 'flex';
                                    overlay.style.flexDirection = 'column';
                                    overlay.style.justifyContent = 'center';
                                    overlay.style.alignItems = 'center';
                                    overlay.style.color = 'white';
                                    overlay.style.fontSize = '24px';
                                    overlay.style.fontWeight = 'bold';
                                    overlay.style.textAlign = 'center';
                                    overlay.innerHTML = 'Registration Complete!<br><span style="font-size: 18px">Redirecting...</span>';
                                    videoContainer.appendChild(overlay);
                                }
                            }
                        } catch (e) {
                            console.error('Error closing connection:', e);
                        }

                        // Force redirect after a short delay
                        setTimeout(() => {
                            console.log('Redirecting to registration page');
                            window.location.href = '/register?success=true';
                        }, 1500);
                    }
                } catch (e) {
                    console.error('Error parsing data channel message:', e);
                }
            });
        });
    }

    /**
     * Set up detection of registration completion
     */
    setupRegistrationDetection() {
        // This is a fallback in case the data channel doesn't work
        // It checks for visual cues in the video that registration is complete

        // Track start time and frame count
        const startTime = Date.now();
        let frameCount = 0;
        let lastCaptureCount = 0;
        let stagnantFrames = 0;

        const checkInterval = setInterval(() => {
            // If video element is gone or we've already initiated a redirect, stop checking
            if (!this.videoElement || !this.videoElement.srcObject || window.registrationRedirectInitiated) {
                clearInterval(checkInterval);
                return;
            }

            frameCount++;
            const elapsedSeconds = (Date.now() - startTime) / 1000;

            // Force completion after 15 seconds regardless of state
            if (elapsedSeconds > 15) {
                console.log('Forcing registration completion after 15 seconds timeout');
                clearInterval(checkInterval);

                // Check if we've already initiated a redirect
                if (window.registrationRedirectInitiated) {
                    return;
                }

                // Set the flag to prevent multiple redirects
                window.registrationRedirectInitiated = true;
                console.log('Setting registrationRedirectInitiated flag to true (timeout)');

                this.stop();
                this.onRegistrationComplete({
                    success: true,
                    message: 'Registration complete (timeout)'
                });

                // Force redirect
                setTimeout(() => {
                    window.location.href = '/register?success=true';
                }, 1500);
                return;
            }

            // Check if we've reached 5 captures
            if (this.captureCount >= 5) {
                console.log('Registration complete detected: 5 captures reached');
                clearInterval(checkInterval);

                // Check if we've already initiated a redirect
                if (window.registrationRedirectInitiated) {
                    return;
                }

                // Set the flag to prevent multiple redirects
                window.registrationRedirectInitiated = true;
                console.log('Setting registrationRedirectInitiated flag to true (5 captures)');

                this.stop();
                this.onRegistrationComplete({
                    success: true,
                    message: 'Registration complete (5 captures)'
                });

                // Force redirect
                setTimeout(() => {
                    window.location.href = '/register?success=true';
                }, 1500);
                return;
            }

            // Check if we're stuck at the same capture count for too long
            if (this.captureCount !== undefined) {
                if (this.captureCount === lastCaptureCount) {
                    stagnantFrames++;
                } else {
                    stagnantFrames = 0;
                    lastCaptureCount = this.captureCount;
                }

                // If we're stuck at the same capture count for 5 seconds and have at least 3 captures,
                // consider registration complete
                if (stagnantFrames > 10 && this.captureCount >= 3) {
                    console.log('Registration complete detected: stagnant with 3+ captures');
                    clearInterval(checkInterval);

                    // Check if we've already initiated a redirect
                    if (window.registrationRedirectInitiated) {
                        return;
                    }

                    // Set the flag to prevent multiple redirects
                    window.registrationRedirectInitiated = true;
                    console.log('Setting registrationRedirectInitiated flag to true (stagnant)');

                    this.stop();
                    this.onRegistrationComplete({
                        success: true,
                        message: 'Registration complete (stagnant)'
                    });

                    // Force redirect
                    setTimeout(() => {
                        window.location.href = '/register?success=true';
                    }, 1500);
                    return;
                }
            }
        }, 500);
    }

    /**
     * Negotiate the WebRTC connection
     */
    async negotiate() {
        try {
            // Create offer with retry mechanism
            let offer;
            try {
                offer = await this.pc.createOffer();
            } catch (offerError) {
                console.warn('Error creating offer, retrying:', offerError);
                // Recreate peer connection and try again
                if (this.pc) {
                    this.pc.close();
                }
                this.createPeerConnection();
                this.localStream.getTracks().forEach(track => {
                    this.pc.addTrack(track, this.localStream);
                });
                offer = await this.pc.createOffer();
            }

            await this.pc.setLocalDescription(offer);

            // Wait for ICE gathering with a shorter timeout and early completion
            await Promise.race([
                new Promise(resolve => {
                    if (this.pc.iceGatheringState === 'complete') {
                        resolve();
                    } else {
                        let candidateCount = 0;
                        const maxCandidates = 5; // Consider connection ready after collecting some candidates

                        const checkState = () => {
                            if (this.pc.iceGatheringState === 'complete') {
                                this.pc.removeEventListener('icegatheringstatechange', checkState);
                                this.pc.removeEventListener('icecandidate', onIceCandidate);
                                resolve();
                            }
                        };

                        const onIceCandidate = (event) => {
                            if (event.candidate) {
                                candidateCount++;
                                // If we have enough candidates, proceed without waiting for complete
                                if (candidateCount >= maxCandidates) {
                                    console.log(`Collected ${candidateCount} ICE candidates, proceeding with connection`);
                                    this.pc.removeEventListener('icegatheringstatechange', checkState);
                                    this.pc.removeEventListener('icecandidate', onIceCandidate);
                                    resolve();
                                }
                            }
                        };

                        this.pc.addEventListener('icegatheringstatechange', checkState);
                        this.pc.addEventListener('icecandidate', onIceCandidate);
                    }
                }),
                new Promise(resolve => setTimeout(resolve, 3000)) // Reduced timeout to 3 seconds
            ]);

            // Get the final offer
            const offer_final = this.pc.localDescription;

            // Send offer to server with retry
            let response;
            let retries = 3;
            let lastError = null;

            while (retries > 0) {
                try {
                    response = await fetch('/webrtc/offer', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            sdp: offer_final.sdp,
                            type: offer_final.type,
                            mode: this.mode,
                            student_id: this.studentId
                        })
                    });

                    if (response.ok) {
                        break;
                    }

                    console.warn(`Server returned ${response.status}, retrying...`);
                    lastError = new Error(`Server returned ${response.status}`);
                } catch (fetchError) {
                    console.warn(`Fetch error: ${fetchError.message}, retries left: ${retries-1}`);
                    lastError = fetchError;
                }

                retries--;
                if (retries > 0) {
                    // Wait before retrying
                    await new Promise(resolve => setTimeout(resolve, 1000));
                } else if (lastError) {
                    throw new Error(`Failed to connect to server: ${lastError.message}`);
                } else {
                    throw new Error('Failed to connect to server after multiple attempts');
                }
            }

            // Get answer from server
            let answer;
            try {
                answer = await response.json();
            } catch (jsonError) {
                console.error('Error parsing server response:', jsonError);
                throw new Error('Invalid response from server');
            }

            if (answer.error) {
                throw new Error(`Server error: ${answer.error}`);
            }

            // Set remote description
            try {
                await this.pc.setRemoteDescription(answer);
            } catch (sdpError) {
                console.error('Error setting remote description:', sdpError);
                throw new Error(`Failed to set remote description: ${sdpError.message}`);
            }

            console.log('Negotiation completed successfully');

            // If this is registration mode, dispatch registration start event
            if (this.mode === 'registration') {
                document.dispatchEvent(new CustomEvent('registration:start', {
                    detail: { processId: 'registration-' + this.studentId }
                }));
            }
            // If this is attendance mode, dispatch attendance start event
            else if (this.mode === 'attendance') {
                document.dispatchEvent(new CustomEvent('attendance:start', {
                    detail: { processId: 'attendance-' + Date.now() }
                }));
            }

        } catch (error) {
            console.error('Error during negotiation:', error);
            this.updateStatus('error');
            
            // Dispatch appropriate stop event on error
            if (this.mode === 'registration') {
                document.dispatchEvent(new CustomEvent('registration:complete', {
                    detail: { processId: 'registration-' + this.studentId }
                }));
            } else if (this.mode === 'attendance') {
                document.dispatchEvent(new CustomEvent('attendance:stop', {
                    detail: { processId: 'attendance-' + Date.now() }
                }));
            }
        }
    }

    /**
     * Update the status of the WebRTC connection
     * @param {string} status - The new status
     * @param {string} message - The status message
     */
    updateStatus(status, message) {
        this.status = status;

        // Update status element if provided
        if (this.statusElement) {
            this.statusElement.textContent = message;
            this.statusElement.className = `status-${status}`;
        }

        // Call status change callback
        this.onStatusChange(status, message);
    }

    // Add this method to handle registration completion
    handleRegistrationComplete() {
        document.dispatchEvent(new CustomEvent('registration:complete', {
            detail: { processId: 'registration-' + this.studentId }
        }));
    }
}

// Export the UnifiedWebRTCClient class
window.UnifiedWebRTCClient = UnifiedWebRTCClient;
