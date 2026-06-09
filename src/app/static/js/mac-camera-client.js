/**
 * Mac Camera Client for Face Recognition Attendance System
 * Specifically designed to work on Chrome for Mac M1 chips
 * Uses a different approach to access the camera
 */

class MacCameraClient {
  constructor(options = {}) {
    // Configuration
    this.videoElement = options.videoElement;
    this.statusElement = options.statusElement;
    this.mode = options.mode || "attendance"; // 'attendance' or 'registration'
    this.studentId = options.studentId;
    this.onStatusChange = options.onStatusChange || (() => {});
    this.onAttendanceMarked = options.onAttendanceMarked || (() => {});
    this.onRegistrationComplete = options.onRegistrationComplete || (() => {});
    this.onFaceDetected = options.onFaceDetected || (() => {});

    // State
    this.stream = null;
    this.status = "disconnected";
    this.captureCount = 0;
    this.captureInterval = null;
    this.processingImage = false;
    this.isCapturing = false;

    // Watchdog timer to detect and fix stalled face detection
    this.lastSuccessfulCapture = Date.now();
    this.watchdogTimer = null;
    this.watchdogInterval = 10000; // 10 seconds

    // Canvas for capturing images
    this.canvas = document.createElement("canvas");
    // Set canvas size to match the video stream's actual resolution for best quality
    this.canvas.width = 1280; // Use 1280x720 for HD, or dynamically set later
    this.canvas.height = 720;
    this.ctx = this.canvas.getContext("2d");

    // Create overlay canvas for face detection visualization
    this.overlayCanvas = document.createElement("canvas");
    this.overlayCanvas.style.position = "absolute";
    this.overlayCanvas.style.top = "0";
    this.overlayCanvas.style.left = "0";
    this.overlayCanvas.style.width = "100%";
    this.overlayCanvas.style.height = "100%";
    this.overlayCanvas.style.pointerEvents = "none";
    this.overlayCtx = this.overlayCanvas.getContext("2d");

    // Bind methods
    this.start = this.start.bind(this);
    this.stop = this.stop.bind(this);
    this.captureAndSendImage = this.captureAndSendImage.bind(this);
    this.updateStatus = this.updateStatus.bind(this);
    this.drawFaceDetectionBox = this.drawFaceDetectionBox.bind(this);
    this.resizeOverlayCanvas = this.resizeOverlayCanvas.bind(this);
    this.setupFallbackCamera = this.setupFallbackCamera.bind(this);
    this.startWatchdogTimer = this.startWatchdogTimer.bind(this);
    this.stopWatchdogTimer = this.stopWatchdogTimer.bind(this);
    this.restartCaptureProcess = this.restartCaptureProcess.bind(this);
  }

  /**
   * Start the camera client
   */
  async start() {
    try {
      this.updateStatus("starting");

      // Dispatch appropriate start event
      if (this.mode === "registration") {
        document.dispatchEvent(
          new CustomEvent("registration:start", {
            detail: { processId: "registration-" + this.studentId },
          }),
        );
      } else if (this.mode === "attendance") {
        document.dispatchEvent(
          new CustomEvent("attendance:start", {
            detail: { processId: "attendance-" + Date.now() },
          }),
        );
      }

      // Get camera stream
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 640 },
          height: { ideal: 480 },
          facingMode: "user",
        },
      });

      // Set video source
      this.videoElement.srcObject = this.stream;
      await this.videoElement.play();

      // After video stream is ready, set canvas size to match video
      if (
        this.videoElement &&
        this.videoElement.videoWidth &&
        this.videoElement.videoHeight
      ) {
        this.canvas.width = this.videoElement.videoWidth;
        this.canvas.height = this.videoElement.videoHeight;
      }

      // Start capturing
      this.isCapturing = true;
      this.captureInterval = setInterval(() => this.captureAndSendImage(), 100);

      // Start watchdog timer
      this.lastSuccessfulCapture = Date.now();
      this.startWatchdogTimer();

      this.updateStatus("connected", "Connected to camera");
    } catch (error) {
      console.error("Error starting camera:", error);
      this.updateStatus("error", "Failed to start camera: " + error.message);

      // Dispatch appropriate stop event on error
      if (this.mode === "registration") {
        document.dispatchEvent(
          new CustomEvent("registration:complete", {
            detail: { processId: "registration-" + this.studentId },
          }),
        );
      } else if (this.mode === "attendance") {
        document.dispatchEvent(
          new CustomEvent("attendance:stop", {
            detail: { processId: "attendance-" + Date.now() },
          }),
        );
      }

      throw error;
    }
  }

  /**
   * Set up fallback camera for Chrome on Mac M1
   */
  async setupFallbackCamera() {
    return new Promise((resolve, reject) => {
      console.log("Setting up fallback camera access method");

      // Create a temporary video element
      const tempVideo = document.createElement("video");
      tempVideo.setAttribute("autoplay", "");
      tempVideo.setAttribute("playsinline", "");
      tempVideo.style.display = "none";
      document.body.appendChild(tempVideo);

      // Try to access the camera using the deprecated API
      // This sometimes works on Chrome for Mac M1 when the standard API fails
      navigator.getUserMedia =
        navigator.getUserMedia ||
        navigator.webkitGetUserMedia ||
        navigator.mozGetUserMedia ||
        navigator.msGetUserMedia;

      if (navigator.getUserMedia) {
        console.log("Using legacy getUserMedia API");

        // Try with more permissive constraints
        const constraints = {
          video: {
            width: { ideal: 320 },
            height: { ideal: 240 },
            frameRate: { ideal: 15 },
          },
          audio: false,
        };

        navigator.getUserMedia(
          constraints,
          (stream) => {
            // Success - we got the stream
            console.log("Successfully obtained stream with fallback method");
            this.stream = stream;

            // Set the stream to our video element
            if (this.videoElement) {
              this.videoElement.srcObject = stream;
              console.log("Set stream to video element");
            }

            // Clean up temp element
            document.body.removeChild(tempVideo);

            resolve();
          },
          (err) => {
            // Error getting stream
            console.error("Error accessing camera with fallback method:", err);

            // Try one more time with even more basic constraints
            console.log("Trying one more time with basic constraints");
            navigator.getUserMedia(
              { video: true, audio: false },
              (stream) => {
                console.log(
                  "Successfully obtained stream with basic constraints",
                );
                this.stream = stream;

                if (this.videoElement) {
                  this.videoElement.srcObject = stream;
                }

                document.body.removeChild(tempVideo);
                resolve();
              },
              (finalErr) => {
                console.error(
                  "Final attempt to access camera failed:",
                  finalErr,
                );
                document.body.removeChild(tempVideo);

                // Check if we're on Safari which might need special handling
                const isSafari = /^((?!chrome|android).)*safari/i.test(
                  navigator.userAgent,
                );
                if (isSafari) {
                  console.log(
                    "Safari detected, trying Safari-specific workaround",
                  );
                  // For Safari, we'll resolve anyway and try to continue
                  // Sometimes Safari reports errors but still works
                  resolve();
                } else {
                  reject(
                    new Error("Failed to access camera with fallback method"),
                  );
                }
              },
            );
          },
        );
      } else {
        // No getUserMedia support
        console.error("No getUserMedia support available in this browser");
        document.body.removeChild(tempVideo);

        // Check if we're on iOS which has special restrictions
        const isIOS =
          /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
        if (isIOS) {
          console.log(
            "iOS detected, camera access may require user interaction",
          );
          // For iOS, we'll show a special message
          reject(
            new Error(
              'iOS requires camera permission. Please tap "Allow" when prompted.',
            ),
          );
        } else {
          reject(
            new Error("No camera access methods available in this browser"),
          );
        }
      }
    });
  }

  /**
   * Resize overlay canvas to match video element dimensions
   */
  resizeOverlayCanvas() {
    if (!this.videoElement || !this.overlayCanvas) return;

    // Get the actual displayed dimensions of the video element
    const videoRect = this.videoElement.getBoundingClientRect();

    // Set the overlay canvas to exactly match the video element's display size
    this.overlayCanvas.style.position = "absolute";
    this.overlayCanvas.style.top = "0";
    this.overlayCanvas.style.left = "0";
    this.overlayCanvas.style.width = `${videoRect.width}px`;
    this.overlayCanvas.style.height = `${videoRect.height}px`;

    // Set the internal canvas dimensions to match the video's intrinsic dimensions
    // This ensures the face detection boxes are properly scaled
    this.overlayCanvas.width = this.canvas.width;
    this.overlayCanvas.height = this.canvas.height;
  }

  /**
   * Stop the camera client
   */
  stop() {
    console.log("Stopping camera client");

    // Set a flag to prevent immediate restart attempts
    this._stopping = true;

    // Stop capturing
    this.isCapturing = false;

    // Clear capture interval
    if (this.captureInterval) {
      clearInterval(this.captureInterval);
      this.captureInterval = null;
      console.log("Cleared capture interval");
    }

    // Clear watchdog timer
    this.stopWatchdogTimer();

    // Stop all tracks with a small delay to ensure proper cleanup
    if (this.stream) {
      // Create a local reference to the stream
      const streamToStop = this.stream;
      this.stream = null;

      // Use setTimeout to delay track stopping slightly
      setTimeout(() => {
        try {
          streamToStop.getTracks().forEach((track) => {
            try {
              track.stop();
              console.log(`Stopped track: ${track.kind}`);
            } catch (trackErr) {
              console.warn(`Error stopping track: ${trackErr.message}`);
            }
          });
        } catch (streamErr) {
          console.warn(`Error stopping stream: ${streamErr.message}`);
        }
      }, 100);
    }

    // Clear video element
    if (this.videoElement) {
      try {
        this.videoElement.srcObject = null;
        this.videoElement.pause();
        console.log("Cleared video element");
      } catch (videoErr) {
        console.warn(`Error clearing video element: ${videoErr.message}`);
      }
    }

    // Dispatch appropriate stop event
    if (this.mode === "registration") {
      document.dispatchEvent(
        new CustomEvent("registration:complete", {
          detail: { processId: "registration-" + this.studentId },
        }),
      );
    } else if (this.mode === "attendance") {
      document.dispatchEvent(
        new CustomEvent("attendance:stop", {
          detail: { processId: "attendance-" + Date.now() },
        }),
      );
    }

    this.updateStatus("disconnected", "Disconnected");
  }

  /**
   * Draw face detection box on overlay canvas
   */
  drawFaceDetectionBox(x, y, width, height) {
    if (!this.overlayCanvas || !this.overlayCtx) return;

    // Ensure the overlay canvas is properly sized
    this.resizeOverlayCanvas();

    // Clear previous drawings
    this.overlayCtx.clearRect(
      0,
      0,
      this.overlayCanvas.width,
      this.overlayCanvas.height,
    );

    // Draw face detection box with green color - only outline, no fill
    this.overlayCtx.strokeStyle = "#00FF00"; // Bright green
    this.overlayCtx.lineWidth = 1.5; // Even thinner line for subtlety
    this.overlayCtx.strokeRect(x, y, width, height);

    // Add a smaller "Face Detected" label
    this.overlayCtx.font = "8px Arial";
    this.overlayCtx.fillStyle = "#00FF00"; // Bright green
    this.overlayCtx.fillText("Face Detected", x, Math.max(y - 2, 10));
  }

  /**
   * Capture and send image to server
   */
  async captureAndSendImage() {
    // Skip if already processing, stopping, or video not ready
    if (
      this.processingImage ||
      this._stopping ||
      !this.videoElement ||
      !this.stream ||
      !this.isCapturing
    ) {
      return;
    }

    // Additional check for video readiness
    try {
      // Check if video dimensions are available
      const hasValidDimensions =
        this.videoElement.videoWidth > 0 && this.videoElement.videoHeight > 0;

      // Skip if video dimensions aren't valid yet
      if (!hasValidDimensions) {
        console.log("Video dimensions not ready yet, skipping frame capture");
        return;
      }

      // Check if video is actually playing
      if (this.videoElement.paused || this.videoElement.ended) {
        console.log("Video is paused or ended, skipping frame capture");
        return;
      }
    } catch (checkError) {
      console.warn("Error checking video state:", checkError);
      return;
    }

    try {
      this.processingImage = true;

      // Log capture attempt
      console.log("Capturing frame from video element");

      try {
        // Draw video frame to canvas
        this.ctx.drawImage(
          this.videoElement,
          0,
          0,
          this.canvas.width,
          this.canvas.height,
        );

        // Check if we actually drew anything (some browsers might fail silently)
        const pixelData = this.ctx.getImageData(0, 0, 1, 1).data;
        const hasContent =
          pixelData[0] > 0 ||
          pixelData[1] > 0 ||
          pixelData[2] > 0 ||
          pixelData[3] > 0;

        if (!hasContent) {
          console.warn("Canvas appears to be empty after drawing video frame");
          // Try again with a slight delay
          setTimeout(() => {
            this.processingImage = false;
          }, 100);
          return;
        }
      } catch (drawError) {
        console.error("Error drawing video to canvas:", drawError);
        // Rethrow to be caught by outer try/catch
        throw drawError;
      }

      // Convert canvas to blob with error handling
      let blob;
      try {
        blob = await new Promise((resolve, reject) => {
          try {
            this.canvas.toBlob(
              (result) => {
                if (result) {
                  resolve(result);
                } else {
                  reject(new Error("Failed to create blob from canvas"));
                }
              },
              "image/jpeg",
              0.8,
            );
          } catch (blobError) {
            reject(blobError);
          }
        });

        if (!blob || blob.size === 0) {
          throw new Error("Generated blob is empty");
        }

        console.log(`Successfully created blob of size ${blob.size} bytes`);
      } catch (blobError) {
        console.error("Error creating blob from canvas:", blobError);
        throw blobError;
      }

      // Create form data
      const formData = new FormData();
      formData.append("image", blob, "capture.jpg");
      formData.append("mode", this.mode);
      formData.append("student_id", this.studentId || "");

      // Send to server with timeout and retry logic
      let response;
      let retryCount = 0;
      const maxRetries = 3;

      console.log("Sending image to server for processing");

      while (retryCount < maxRetries) {
        try {
          // Skip if we're stopping
          if (this._stopping || !this.isCapturing) {
            console.log("Skipping server request because client is stopping");
            return;
          }

          // Add cache-busting parameter to prevent caching issues
          const cacheBuster = new Date().getTime();
          response = await fetch(`/process_image?t=${cacheBuster}`, {
            method: "POST",
            body: formData,
            // Add timeout using AbortController
            signal: AbortSignal.timeout(5000), // 5 second timeout
          });

          if (response.ok) {
            console.log("Server responded successfully");
            break; // Success, exit retry loop
          } else {
            console.warn(
              `Server returned ${response.status}, retry ${retryCount + 1}/${maxRetries}`,
            );
            retryCount++;
            if (retryCount < maxRetries) {
              await new Promise((r) => setTimeout(r, 500)); // Wait 500ms before retry
            }
          }
        } catch (fetchError) {
          console.warn(
            `Fetch error: ${fetchError.message}, retry ${retryCount + 1}/${maxRetries}`,
          );
          retryCount++;
          if (retryCount < maxRetries) {
            await new Promise((r) => setTimeout(r, 500)); // Wait 500ms before retry
          }
        }
      }

      if (!response || !response.ok) {
        throw new Error(
          `Server returned ${response ? response.status : "no response"} after ${maxRetries} retries`,
        );
      }

      // Parse JSON with error handling
      let result;
      try {
        const text = await response.text();
        result = JSON.parse(text);
        console.log("Successfully parsed server response:", result);

        // Update last successful capture time
        this.lastSuccessfulCapture = Date.now();
      } catch (jsonError) {
        console.error("Error parsing JSON response:", jsonError);
        throw new Error("Invalid JSON response from server");
      }

      // Handle different response types
      if (result.faces_detected) {
        // Face detected
        console.log(`Detected ${result.faces_detected} face(s) in frame`);
        this.onFaceDetected({
          type: "face_detected",
          count: result.faces_detected,
          capture_count: result.capture_count || 0,
        });

        // Draw face detection boxes for multiple faces in attendance mode
        if (
          this.mode === "attendance" &&
          result.faces &&
          Array.isArray(result.faces)
        ) {
          this.overlayCtx.clearRect(
            0,
            0,
            this.overlayCanvas.width,
            this.overlayCanvas.height,
          );
          for (const face of result.faces) {
            this.drawFaceDetectionBox(face.x, face.y, face.width, face.height);
          }
        } else if (result.face_rect) {
          // fallback for single face
          const { x, y, width, height } = result.face_rect;
          console.log(
            `Drawing face detection box at (${x},${y}) with size ${width}x${height}`,
          );
          this.drawFaceDetectionBox(x, y, width, height);
        }

        // Update capture count for registration
        if (
          this.mode === "registration" &&
          result.capture_count !== undefined
        ) {
          this.captureCount = result.capture_count;
          console.log(`Updated capture count: ${this.captureCount}`);

          // Check if registration is complete
          if (result.registration_complete) {
            console.log("Registration complete! Stopping capture immediately.");

            // IMMEDIATELY stop the capture interval to prevent extra frames
            if (this.captureInterval) {
              clearInterval(this.captureInterval);
              this.captureInterval = null;
              console.log("Capture interval cleared");
            }

            // Notify callback
            this.onRegistrationComplete({ success: true });

            // Stop processing
            this.stop();
          }
        }
      } else if (
        this.mode === "registration" &&
        result.capture_count !== undefined
      ) {
        // Registration mode: server returns capture_count even when no face is newly detected
        this.captureCount = result.capture_count;
        console.log(
          `Updated capture count (no new face): ${this.captureCount}`,
        );

        this.onFaceDetected({
          type: "capture_progress",
          capture_count: this.captureCount,
          total: result.images_needed || 5,
        });

        if (result.registration_complete) {
          console.log("Registration complete! Stopping capture immediately.");
          if (this.captureInterval) {
            clearInterval(this.captureInterval);
            this.captureInterval = null;
          }
          this.onRegistrationComplete({ success: true });
          this.stop();
        }
      } else {
        // No faces detected, clear any face detection box
        if (this.overlayCtx) {
          this.overlayCtx.clearRect(
            0,
            0,
            this.overlayCanvas.width,
            this.overlayCanvas.height,
          );
        }
      }

      const markedFace =
        result.faces && Array.isArray(result.faces)
          ? result.faces.find((f) => f.type === "attendance_marked")
          : null;
      const alreadyMarkedFace =
        result.faces && Array.isArray(result.faces)
          ? result.faces.find((f) => f.type === "already_marked_today")
          : null;

      if (markedFace) {
        console.log(
          `Attendance marked for ${markedFace.name} (ID: ${markedFace.student_id})`,
        );
        this.onAttendanceMarked({
          type: "attendance_marked",
          name: markedFace.name,
          student_id: markedFace.student_id,
          timestamp: result.timestamp,
        });
      } else if (alreadyMarkedFace) {
        console.log(
          `Already marked today for ${alreadyMarkedFace.name} (ID: ${alreadyMarkedFace.student_id})`,
        );
        this.onAttendanceMarked({
          type: "already_marked_today",
          name: alreadyMarkedFace.name,
          student_id: alreadyMarkedFace.student_id,
          timestamp: result.timestamp,
        });
      }
    } catch (error) {
      console.error("Error capturing/sending image:", error);

      // Don't count network errors against us
      if (
        error.name === "AbortError" ||
        error.name === "TypeError" ||
        (error.message && error.message.includes("network"))
      ) {
        console.log("Network error occurred, will try again next interval");
      }

      // Clear any face detection box on error
      if (this.overlayCtx) {
        this.overlayCtx.clearRect(
          0,
          0,
          this.overlayCanvas.width,
          this.overlayCanvas.height,
        );
      }
    } finally {
      this.processingImage = false;
    }
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
      if (
        timeSinceLastCapture > this.watchdogInterval &&
        this.isCapturing &&
        !this._stopping
      ) {
        console.log(
          `Watchdog: Face detection appears stalled (${timeSinceLastCapture}ms). Restarting capture process...`,
        );

        // Attempt to restart the capture process
        this.restartCaptureProcess();
      }
    }, 5000); // Check every 5 seconds

    console.log("Watchdog timer started");
  }

  /**
   * Stop the watchdog timer
   */
  stopWatchdogTimer() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
      console.log("Watchdog timer stopped");
    }
  }

  /**
   * Restart the capture process if it appears to be stalled
   */
  async restartCaptureProcess() {
    // Only proceed if we're not already stopping or processing
    if (this._stopping || this.processingImage) {
      return;
    }

    console.log("Attempting to restart capture process...");

    try {
      // Clear existing capture interval
      if (this.captureInterval) {
        clearInterval(this.captureInterval);
        this.captureInterval = null;
      }

      // Reset processing flag
      this.processingImage = false;

      // Restart the capture interval
      this.isCapturing = true;
      this.captureInterval = setInterval(() => this.captureAndSendImage(), 100);

      // Reset the last successful capture time
      this.lastSuccessfulCapture = Date.now();

      console.log("Capture process restarted successfully");
    } catch (error) {
      console.error("Error restarting capture process:", error);
    }
  }
}

// Export the class
window.MacCameraClient = MacCameraClient;
