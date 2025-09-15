#!/usr/bin/env python3
"""
Hybrid Preview Monitor for ComfyUI
Combines Pygame window with embedded web browser for best of both worlds
"""

import os
import sys
import json
import base64
import threading
import time
import hashlib
import webbrowser
from io import BytesIO
import numpy as np
from PIL import Image
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

# Pygame imports
try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    print("--------------Hybrid Preview Monitor: Pygame not available")

# Web browser imports
try:
    import webview
    WEBVIEW_AVAILABLE = True
except ImportError:
    WEBVIEW_AVAILABLE = False
    print("--------------Hybrid Preview Monitor: Webview not available. Install with: pip install pywebview")

class HybridPreviewMonitor:
    """
    Hybrid preview monitor using Pygame window with embedded web browser
    """
    
    
    _windows = {}  # monitor_idx -> {"thread": Thread, "webview": webview, "server": HTTPServer, "images": [PIL.Image], "current_idx": int, "lock": Lock, "visible": bool, "display_mode": str, "compare_images": [PIL.Image], "zoom": float, "pan_x": float, "pan_y": float}
    _server = None
    _server_thread = None
    _port = 5060
    _image_cache = {}
    
    @classmethod
    def get_monitors(cls):
        """Get available monitors"""
        monitors = []
        if PYGAME_AVAILABLE:
            try:
                num_displays = pygame.display.get_num_displays()
                for i in range(num_displays):
                    info = pygame.display.Info(display=i)
                    monitors.append(f"Monitor {i} ({info.current_w}x{info.current_h})")
            except Exception:
                pass
        if not monitors:
            monitors = ["Monitor 0 (1920x1080)"]

        print("Hybrid Preview Monitor: Available monitors:", monitors)
        return monitors
    
    @classmethod
    def get_target_resolutions(cls):
        """Get target resolutions based on available monitors"""
        resolutions = set()
        
        # Add resolutions from available monitors
        if PYGAME_AVAILABLE:
            try:
                num_displays = pygame.display.get_num_displays()
                for i in range(num_displays):
                    info = pygame.display.Info(display=i)
                    resolutions.add(f"{info.current_w}x{info.current_h}")
            except Exception:
                pass
        
        # Always add common resolutions as fallback options
        # Add common resolutions (both landscape and portrait)
        resolutions.update({
            # Landscape resolutions
            "1920x1080", "2560x1440", "3840x2160", "1366x768", "1440x900", "1680x1050",
            # Portrait resolutions
            "1080x1920", "1440x2560", "2160x3840", "768x1366", "900x1440", "1050x1680",
            # Other common resolutions
            "1280x720", "1600x900", "1920x1200", "2560x1600", "3440x1440"
        })
        
        # Convert to sorted list
        resolution_list = sorted(list(resolutions), key=lambda x: int(x.split('x')[0]) * int(x.split('x')[1]), reverse=True)
        print("Hybrid Preview Monitor: Available resolutions:", resolution_list)
        return resolution_list
    
    @classmethod
    def INPUT_TYPES(cls):
        """Define input types for ComfyUI"""
        monitor_list = cls.get_monitors()
        target_resolutions = cls.get_target_resolutions()
        return {
            "required": {
                "images": ("IMAGE",),
                "monitor": (monitor_list, {"default": monitor_list[0]}),
                "power_state": (["On", "Off"], {"default": "On"}),
                "display_mode": (["single", "comparison", "slideshow"], {"default": "single"}),
                "fit_mode": (["none", "width", "height", "fit", "fill", "distort", "center"], {"default": "fit"}),
                "target_resolution": (target_resolutions, {"default": target_resolutions[0]}),
                "gain": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "gamma": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "saturation": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.1}),
                "white_matte": ("BOOLEAN", {"default": False}),
                "fps_mode": (["smart", "15fps", "30fps", "60fps"], {"default": "smart"}),
            },
            "optional": {
                "compare_images": ("IMAGE",),
            }
        }

    # ComfyUI required attributes
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "display_image"
    OUTPUT_NODE = True
    CATEGORY = "image/preview"
    
    @classmethod
    def get_monitors(cls):
        """Get available monitors"""
        try:
            import screeninfo
            monitors = screeninfo.get_monitors()
            return [f"Monitor {i}: {m.width}x{m.height}" for i, m in enumerate(monitors)]
        except ImportError:
            return ["Monitor 0: 1920x1080"]
    
    @classmethod
    def start_web_server(cls, port=5060):
        """Start the web server for embedded browser"""
        if cls._server is not None:
            return True
        
        cls._port = port
        
        class PreviewHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/':
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(cls._get_hybrid_html().encode())
                elif self.path.startswith('/api/image/'):
                    image_id = self.path.split('/')[-1]
                    if image_id in cls._image_cache:
                        self.send_response(200)
                        self.send_header('Content-type', 'image/png')
                        self.end_headers()
                        img_data = cls._image_cache[image_id]['data']
                        img_bytes = base64.b64decode(img_data)
                        self.wfile.write(img_bytes)
                    else:
                        self.send_response(404)
                        self.end_headers()
                elif self.path == '/api/data':
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    data = {
                        'images': cls._current_images,
                        'settings': cls._current_settings
                    }
                    self.wfile.write(json.dumps(data).encode())
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def log_message(self, format, *args):
                pass
        
        try:
            cls._server = HTTPServer(('localhost', port), PreviewHandler)
            cls._server_thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
            cls._server_thread.start()
            return True
        except Exception as e:
            return False
    
    @classmethod
    def _get_hybrid_html(cls):
        """Get HTML content for hybrid interface"""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hybrid Preview Monitor</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            background: #000;
            overflow: hidden;
            font-family: Arial, sans-serif;
            width: 100vw;
            height: 100vh;
        }
        
        #canvas-container {
            position: relative;
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        #main-canvas {
            max-width: 100%;
            max-height: 100%;
            cursor: crosshair;
        }
        
        #controls {
            position: fixed;
            top: 10px;
            left: 10px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
            z-index: 1000;
        }
        
        #status {
            position: fixed;
            bottom: 10px;
            right: 10px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
            z-index: 1000;
        }
        
        #mode-toggle {
            position: fixed;
            top: 10px;
            right: 10px;
            background: rgba(0, 0, 0, 0.8);
            color: white;
            padding: 10px;
            border-radius: 5px;
            font-size: 12px;
            z-index: 1000;
        }
        
        button {
            background: #333;
            color: white;
            border: 1px solid #555;
            padding: 5px 10px;
            margin: 2px;
            border-radius: 3px;
            cursor: pointer;
        }
        
        button:hover {
            background: #555;
        }
    </style>
</head>
<body>
    <div id="canvas-container">
        <canvas id="main-canvas"></canvas>
    </div>
    
    <div id="controls">
        <div>Hybrid Preview Monitor</div>
        <div>Mode: <span id="mode">Single</span></div>
        <div>Images: <span id="image-count">0</span></div>
        <div>Zoom: <span id="zoom">100%</span></div>
    </div>
    
    <div id="status">
        <div>Mouse: <span id="mouse-pos">0, 0</span></div>
        <div>FPS: <span id="fps">0</span></div>
    </div>
    
    <div id="mode-toggle">
        <button onclick="toggleMode()">Toggle Mode</button>
        <button onclick="resetView()">Reset</button>
        <button onclick="toggleComparison()">Compare</button>
    </div>
    
    <script>
        class HybridPreviewMonitor {
            constructor() {
                this.canvas = document.getElementById('main-canvas');
                this.ctx = this.canvas.getContext('2d');
                
                this.images = [];
                this.compareImages = [];
                this.currentImageIndex = 0;
                this.settings = {};
                
                this.zoom = 1.0;
                this.panX = 0;
                this.panY = 0;
                this.mouseX = 0;
                this.mouseY = 0;
                this.dragging = false;
                this.lastMousePos = { x: 0, y: 0 };
                
                this.fps = 0;
                this.lastTime = 0;
                this.frameCount = 0;
                
                this.setupEventListeners();
                this.resizeCanvas();
                this.loadData();
                this.animate();
            }
            
            setupEventListeners() {
                // Mouse events
                this.canvas.addEventListener('mousemove', (e) => {
                    const rect = this.canvas.getBoundingClientRect();
                    this.mouseX = e.clientX - rect.left;
                    this.mouseY = e.clientY - rect.top;
                    
                    if (this.dragging) {
                        const dx = this.mouseX - this.lastMousePos.x;
                        const dy = this.mouseY - this.lastMousePos.y;
                        this.panX += dx;
                        this.panY += dy;
                    }
                    
                    this.lastMousePos = { x: this.mouseX, y: this.mouseY };
                });
                
                this.canvas.addEventListener('mousedown', (e) => {
                    if (e.button === 0) {
                        this.dragging = true;
                    }
                });
                
                this.canvas.addEventListener('mouseup', (e) => {
                    this.dragging = false;
                });
                
                this.canvas.addEventListener('wheel', (e) => {
                    e.preventDefault();
                    
                    // Get mouse position relative to canvas
                    const rect = this.canvas.getBoundingClientRect();
                    const mouseX = e.clientX - rect.left;
                    const mouseY = e.clientY - rect.top;
                    
                    // Store old zoom
                    const oldZoom = this.zoom;
                    const delta = e.deltaY > 0 ? 0.9 : 1.1;
                    this.zoom = Math.max(0.1, Math.min(10, this.zoom * delta));
                    
                    // Adjust pan to keep mouse position fixed during zoom
                    if (oldZoom !== this.zoom) {
                        // Calculate the point under the mouse before zoom
                        const worldX = (mouseX - this.canvas.width / 2 - this.panX) / oldZoom;
                        const worldY = (mouseY - this.canvas.height / 2 - this.panY) / oldZoom;
                        
                        // Calculate new pan to keep the same world point under the mouse
                        this.panX = mouseX - this.canvas.width / 2 - worldX * this.zoom;
                        this.panY = mouseY - this.canvas.height / 2 - worldY * this.zoom;
                    }
                    
                    this.updateDisplay();
                });
                
                // Keyboard events
                document.addEventListener('keydown', (e) => {
                    switch(e.key) {
                        case 'ArrowLeft':
                            this.previousImage();
                            break;
                        case 'ArrowRight':
                            this.nextImage();
                            break;
                        case 'r':
                        case 'R':
                            this.resetView();
                            break;
                        case 'c':
                        case 'C':
                            this.toggleComparison();
                            break;
                        case 's':
                        case 'S':
                            this.toggleSlideshow();
                            break;
                    }
                });
                
                window.addEventListener('resize', () => {
                    this.resizeCanvas();
                });
            }
            
            async loadData() {
                try {
                    const response = await fetch('/api/data');
                    const data = await response.json();
                    this.images = data.images || [];
                    this.settings = data.settings || {};
                    this.updateDisplay();
                } catch (error) {
                    console.error('Error loading data:', error);
                }
            }
            
            resizeCanvas() {
                this.canvas.width = window.innerWidth;
                this.canvas.height = window.innerHeight;
            }
            
            async loadImage(imageId) {
                try {
                    const response = await fetch(`/api/image/${imageId}`);
                    const blob = await response.blob();
                    return new Promise((resolve) => {
                        const img = new Image();
                        img.onload = () => resolve(img);
                        img.src = URL.createObjectURL(blob);
                    });
                } catch (error) {
                    console.error('Error loading image:', error);
                    return null;
                }
            }
            
            async updateDisplay() {
                if (this.images.length === 0) return;
                
                const currentImage = await this.loadImage(this.images[this.currentImageIndex]);
                if (!currentImage) return;
                
                this.drawImage(currentImage);
                this.updateUI();
            }
            
            drawImage(img) {
                const canvas = this.canvas;
                const ctx = this.ctx;
                
                // Clear canvas
                ctx.fillStyle = this.settings.white_matte ? '#ffffff' : '#000000';
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                
                // Calculate image dimensions
                const imgWidth = img.width * this.zoom;
                const imgHeight = img.height * this.zoom;
                
                // Calculate position
                const x = (canvas.width - imgWidth) / 2 + this.panX;
                const y = (canvas.height - imgHeight) / 2 + this.panY;
                
                // Draw image
                ctx.drawImage(img, x, y, imgWidth, imgHeight);
                
                // Draw comparison split line if in comparison mode
                if (this.settings.display_mode === 'comparison' && this.compareImages.length > 0) {
                    this.drawComparison(img);
                }
            }
            
            async drawComparison(mainImg) {
                const compareImg = await this.loadImage(this.compareImages[0]);
                if (!compareImg) return;
                
                const canvas = this.canvas;
                const ctx = this.ctx;
                
                // Resize comparison image to match main image size for unified comparison
                const mainWidth = mainImg.width * this.zoom;
                const mainHeight = mainImg.height * this.zoom;
                const x = (canvas.width - mainWidth) / 2 + this.panX;
                const y = (canvas.height - mainHeight) / 2 + this.panY;
                
                // Create clipping path for right side
                ctx.save();
                ctx.beginPath();
                ctx.rect(this.mouseX, 0, canvas.width - this.mouseX, canvas.height);
                ctx.clip();
                // Draw comparison image with same dimensions as main image
                ctx.drawImage(compareImg, x, y, mainWidth, mainHeight);
                ctx.restore();
                
                // Draw split line
                ctx.strokeStyle = this.settings.white_matte ? '#000000' : '#ffff00';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(this.mouseX, 0);
                ctx.lineTo(this.mouseX, canvas.height);
                ctx.stroke();
            }
            
            updateUI() {
                document.getElementById('mode').textContent = this.settings.display_mode || 'Single';
                document.getElementById('image-count').textContent = this.images.length;
                document.getElementById('zoom').textContent = Math.round(this.zoom * 100) + '%';
                document.getElementById('mouse-pos').textContent = `${Math.round(this.mouseX)}, ${Math.round(this.mouseY)}`;
                document.getElementById('fps').textContent = this.fps;
            }
            
            nextImage() {
                if (this.images.length > 1) {
                    this.currentImageIndex = (this.currentImageIndex + 1) % this.images.length;
                    this.updateDisplay();
                }
            }
            
            previousImage() {
                if (this.images.length > 1) {
                    this.currentImageIndex = (this.currentImageIndex - 1 + this.images.length) % this.images.length;
                    this.updateDisplay();
                }
            }
            
            resetView() {
                this.zoom = 1.0;
                this.panX = 0;
                this.panY = 0;
                this.updateDisplay();
            }
            
            toggleComparison() {
                if (this.settings.display_mode === 'comparison') {
                    this.settings.display_mode = 'single';
                } else if (this.compareImages.length > 0) {
                    this.settings.display_mode = 'comparison';
                }
                this.updateDisplay();
            }
            
            toggleSlideshow() {
                if (this.settings.display_mode === 'slideshow') {
                    this.settings.display_mode = 'single';
                } else {
                    this.settings.display_mode = 'slideshow';
                }
                this.updateDisplay();
            }
            
            animate() {
                const now = performance.now();
                this.frameCount++;
                
                if (now - this.lastTime >= 1000) {
                    this.fps = Math.round((this.frameCount * 1000) / (now - this.lastTime));
                    this.frameCount = 0;
                    this.lastTime = now;
                }
                
                this.updateUI();
                requestAnimationFrame(() => this.animate());
            }
        }
        
        // Global functions for buttons
        function toggleMode() {
            if (window.previewMonitor) {
                window.previewMonitor.toggleSlideshow();
            }
        }
        
        function resetView() {
            if (window.previewMonitor) {
                window.previewMonitor.resetView();
            }
        }
        
        function toggleComparison() {
            if (window.previewMonitor) {
                window.previewMonitor.toggleComparison();
            }
        }
        
        // Initialize the preview monitor
        document.addEventListener('DOMContentLoaded', () => {
            window.previewMonitor = new HybridPreviewMonitor();
        });
    </script>
</body>
</html>'''
    
    @classmethod
    def _process_image(cls, image_data):
        """Process image data and return image ID"""
        # Convert tensor to numpy array
        if hasattr(image_data, 'cpu'):
            image = 255.0 * image_data.cpu().numpy()
        elif hasattr(image_data, 'numpy'):
            image = 255.0 * image_data.numpy()
        else:
            image = 255.0 * image_data
        
        # Ensure correct shape
        if len(image.shape) == 4:
            image = image.squeeze(0)
        
        # Convert to PIL Image
        pil_img = Image.fromarray(np.clip(image, 0, 255).astype(np.uint8))
        
        # Convert to base64 for web transmission
        buffer = BytesIO()
        pil_img.save(buffer, format='PNG')
        img_str = base64.b64encode(buffer.getvalue()).decode()
        
        # Generate unique ID
        img_id = hashlib.md5(img_str.encode()).hexdigest()[:16]
        
        # Cache the image (store PIL Image directly for easier access)
        cls._image_cache[img_id] = pil_img
        
        return img_id
    
    @classmethod
    def display_image(cls, images, monitor=0, power_state="On", display_mode="single", 
                     fit_mode="fit", target_resolution="1920x1080", 
                     gain=1.0, gamma=1.0, saturation=1.0, white_matte=False, 
                     fps_mode="smart", compare_images=None):
        """Display images in hybrid window"""
        
        try:
            images_len = len(images) if images is not None else 0
        except:
            images_len = "unknown (tensor)"
        try:
            compare_len = len(compare_images) if compare_images is not None else 0
        except:
            compare_len = "unknown (tensor)"
        
        if power_state == "Off":
            return (images,)
        
        if not PYGAME_AVAILABLE:
            print("❌ [ERROR] Pygame not available")
            return (images,)
        
        if not WEBVIEW_AVAILABLE:
            print("❌ [ERROR] Webview not available. Install with: pip install pywebview")
            return (images,)
        
        # Start web server if not running
        if cls._server is None:
            if not cls.start_web_server():
                print("❌ [ERROR] Failed to start web server")
                return (images,)
        
        # Process images
        processed_images = []
        try:
            images_list = list(images) if hasattr(images, '__iter__') else [images]
            for i, image in enumerate(images_list):
                img_id = cls._process_image(image)
                processed_images.append(img_id)
        except Exception as e:
            print(f"❌ [ERROR] Error processing images: {e}")
            return (images,)
        
        compare_processed = []
        if compare_images is not None:
            try:
                compare_list = list(compare_images) if hasattr(compare_images, '__iter__') else [compare_images]
                for i, image in enumerate(compare_list):
                    img_id = cls._process_image(image)
                    compare_processed.append(img_id)
            except Exception as e:
                print(f"❌ [ERROR] Error processing compare images: {e}")
                compare_processed = []
        
        # Extract monitor index from monitor string
        try:
            if isinstance(monitor, str):
                # Extract number from "Monitor 1: 1080x1920" format
                monitor_idx = int(monitor.split(" ")[1].split(":")[0])
            else:
                monitor_idx = int(monitor)
        except Exception as e:
            monitor_idx = 0
        
        # Prepare settings
        settings = {
            'display_mode': display_mode,
            'fit_mode': fit_mode,
            'target_resolution': target_resolution,
            'gain': gain,
            'gamma': gamma,
            'saturation': saturation,
            'white_matte': white_matte,
            'fps_mode': fps_mode,
            'monitor': monitor
        }
        
        # Update current data
        cls._current_images = processed_images
        cls._current_settings = settings
        
        # Check existing windows
        # Create or update window
        if monitor_idx not in cls._windows:
            cls._create_hybrid_window(monitor_idx, processed_images, compare_processed, settings)
        else:
            # Update existing window data
            with cls._windows[monitor_idx]["lock"]:
                cls._windows[monitor_idx]["images"] = processed_images
                cls._windows[monitor_idx]["compare_images"] = compare_processed
                cls._windows[monitor_idx]["settings"] = settings
                # Force window to refresh by resetting current index
                cls._windows[monitor_idx]["current_idx"] = 0
                cls._windows[monitor_idx]["refresh_needed"] = True
        
        return (images,)
    
    @classmethod
    def _create_hybrid_window(cls, monitor_idx, images, compare_images, settings):
        """Create a hybrid window - uses Pygame with enhanced features"""
        cls._create_pygame_fallback(monitor_idx, images, compare_images, settings)
    
    @classmethod
    def _create_pygame_fallback(cls, monitor_idx, images, compare_images, settings):
        """Enhanced Pygame window with hybrid features"""
        
        if not PYGAME_AVAILABLE:
            print("❌ [ERROR] Pygame not available")
            return
        
        def pygame_window_thread():
            try:
                # Initialize pygame
                pygame.init()
                
                # Force initialization of all closure variables - ensure they're always defined
                if 'settings' not in locals() or settings is None:
                    settings = {}
                if 'images' not in locals() or images is None:
                    images = []
                if 'compare_images' not in locals() or compare_images is None:
                    compare_images = []
                
                # Get target resolution from settings
                target_resolution = settings.get('target_resolution', '1920x1080')
                try:
                    target_w, target_h = map(int, target_resolution.split('x'))
                except Exception:
                    target_w, target_h = 1920, 1080
                
                # Use target resolution directly as window size
                width, height = target_w, target_h
                
                # Set window position to specified monitor BEFORE creating window
                try:
                    monitor_pos = cls._get_monitor_position(monitor_idx)
                    if monitor_pos:
                        os.environ['SDL_VIDEO_WINDOW_POS'] = f"{monitor_pos[0]},{monitor_pos[1]}"
                    else:
                        # Clear position for monitor 0
                        if 'SDL_VIDEO_WINDOW_POS' in os.environ:
                            del os.environ['SDL_VIDEO_WINDOW_POS']
                except Exception as e:
                    print(f"Warning: Could not set window position for monitor {monitor_idx}: {e}")
                
                # Create window on specified monitor
                screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
                pygame.display.set_caption(f'Hybrid Preview Monitor {monitor_idx} (Enhanced Pygame) - {width}x{height}')
                
                # Enhanced features
                clock = pygame.time.Clock()
                running = True
                current_idx = 0
                
                # Ensure settings is always defined before using it
                if settings is None:
                    settings = {}
                
                display_mode = settings.get('display_mode', 'single')
                zoom = 1.0
                pan_x, pan_y = 0, 0
                dragging = False
                last_mouse_pos = (0, 0)
                
                # Data refresh tracking
                last_images = images
                last_compare_images = compare_images
                last_settings = settings
                
                # Slideshow timing
                slideshow_timer = 0
                slideshow_interval = 2000  # 2 seconds per image
                
                # Font for UI
                try:
                    font = pygame.font.Font(None, 24)
                except:
                    font = pygame.font.SysFont('Arial', 24)
                
                loop_count = 0
                while running:
                    loop_count += 1
                    
                    # Check if window should be closed
                    if monitor_idx in cls._windows and not cls._windows[monitor_idx]["visible"]:
                        running = False
                        break
                    
                    # Check for data updates
                    if monitor_idx in cls._windows:
                        with cls._windows[monitor_idx]["lock"]:
                            window_data = cls._windows[monitor_idx]
                            if (window_data.get("refresh_needed", False) or 
                                window_data.get("images") != last_images or
                                window_data.get("compare_images") != last_compare_images or
                                window_data.get("settings") != last_settings):
                                
                                images = window_data.get("images", images)
                                compare_images = window_data.get("compare_images", compare_images)
                                new_settings = window_data.get("settings", settings)
                                if new_settings is not None:
                                    settings = new_settings
                                current_idx = window_data.get("current_idx", 0)
                                display_mode = settings.get('display_mode', 'single') if settings else 'single'
                                
                                # Check if resolution changed and recreate window if needed
                                new_target_resolution = settings.get('target_resolution', '1920x1080')
                                try:
                                    new_w, new_h = map(int, new_target_resolution.split('x'))
                                    if new_w != width or new_h != height:
                                        # Update window size
                                        width, height = new_w, new_h
                                        screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
                                        pygame.display.set_caption(f'Hybrid Preview Monitor {monitor_idx} (Enhanced Pygame) - {width}x{height}')
                                        print(f"✅ [DEBUG] Window resized to {width}x{height}")
                                except Exception as e:
                                    print(f"⚠️ [WARNING] Could not parse target resolution {new_target_resolution}: {e}")
                                
                                # Update tracking variables
                                last_images = images
                                last_compare_images = compare_images
                                last_settings = settings
                                
                                # Clear refresh flag
                                window_data["refresh_needed"] = False
                                
                    for event in pygame.event.get():
                        if event.type == pygame.QUIT:
                            running = False
                        elif event.type == pygame.KEYDOWN:
                            # Check for modifier keys
                            ctrl_pressed = pygame.key.get_pressed()[pygame.K_LCTRL] or pygame.key.get_pressed()[pygame.K_RCTRL]
                            
                            if event.key == pygame.K_ESCAPE:
                                running = False
                            elif event.key == pygame.K_SPACE and len(images) > 1:
                                current_idx = (current_idx + 1) % len(images)
                                slideshow_timer = 0  # Reset slideshow timer
                            elif event.key == pygame.K_c and ctrl_pressed:
                                # Toggle comparison mode (only if we have enough images)
                                if display_mode != 'comparison':
                                    if len(images) > 1 or (compare_images and len(compare_images) > 0):
                                        display_mode = 'comparison'
                                    else:
                                        print(f"🔍 [DEBUG] Cannot switch to comparison mode: need at least 2 images")
                                else:
                                    display_mode = 'single'
                            elif event.key == pygame.K_s and ctrl_pressed:
                                # Toggle slideshow mode
                                display_mode = 'slideshow' if display_mode != 'slideshow' else 'single'
                                slideshow_timer = 0  # Reset slideshow timer
                            elif event.key == pygame.K_r and ctrl_pressed:
                                # Reset zoom and pan
                                zoom = 1.0
                                pan_x, pan_y = 0, 0
                        elif event.type == pygame.MOUSEBUTTONDOWN:
                            if event.button == 1:  # Left click
                                dragging = True
                                last_mouse_pos = event.pos
                        elif event.type == pygame.MOUSEBUTTONUP:
                            if event.button == 1:
                                dragging = False
                        elif event.type == pygame.MOUSEMOTION:
                            if dragging:
                                dx = event.pos[0] - last_mouse_pos[0]
                                dy = event.pos[1] - last_mouse_pos[1]
                                pan_x += dx
                                pan_y += dy
                                last_mouse_pos = event.pos
                        elif event.type == pygame.MOUSEWHEEL:
                            # Zoom with mouse wheel - zoom around mouse position
                            mouse_x, mouse_y = pygame.mouse.get_pos()
                            old_zoom = zoom
                            zoom_factor = 1.1 if event.y > 0 else 0.9
                            zoom = max(0.1, min(5.0, zoom * zoom_factor))
                            
                            # Adjust pan to keep mouse position fixed during zoom
                            if old_zoom != zoom:
                                # Calculate the point under the mouse before zoom
                                world_x = (mouse_x - width // 2 - pan_x) / old_zoom
                                world_y = (mouse_y - height // 2 - pan_y) / old_zoom
                                
                                # Calculate new pan to keep the same world point under the mouse
                                pan_x = mouse_x - width // 2 - world_x * zoom
                                pan_y = mouse_y - height // 2 - world_y * zoom
                    
                    # Clear screen
                    screen.fill((0, 0, 0))
                    
                    # Display current image(s)
                    if images and current_idx < len(images):
                        try:
                            # Get PIL Image from cache
                            pil_img = cls._image_cache.get(images[current_idx])
                            if pil_img:
                                # Convert to pygame surface
                                img_surface = pygame.image.fromstring(pil_img.tobytes(), pil_img.size, pil_img.mode)
                                
                                # Apply zoom and pan
                                if zoom != 1.0:
                                    new_size = (int(img_surface.get_width() * zoom), int(img_surface.get_height() * zoom))
                                    img_surface = pygame.transform.scale(img_surface, new_size)
                                
                                # Calculate position with pan
                                img_rect = img_surface.get_rect()
                                img_rect.centerx = width // 2 + pan_x
                                img_rect.centery = height // 2 + pan_y
                                
                                # Display image
                                if display_mode == 'comparison':
                                    # Comparison mode - split screen with unified resolution
                                    mouse_x, mouse_y = pygame.mouse.get_pos()
                                    split_x = mouse_x
                                    
                                    # Get comparison image - try compare_images first, then fallback to next image
                                    comp_pil_img = None
                                    if compare_images and len(compare_images) > 0:
                                        # Use compare_images input
                                        comp_pil_img = cls._image_cache.get(compare_images[0])
                                    elif len(images) > 1:
                                        # Fallback to next image in images list
                                        next_idx = (current_idx + 1) % len(images)
                                        comp_pil_img = cls._image_cache.get(images[next_idx])
                                    if comp_pil_img:
                                        # Resize comparison image to match main image size
                                        comp_surface = pygame.image.fromstring(comp_pil_img.tobytes(), comp_pil_img.size, comp_pil_img.mode)
                                        comp_surface = pygame.transform.scale(comp_surface, img_surface.get_size())
                                        
                                        # Apply same zoom and pan to both images
                                        if zoom != 1.0:
                                            comp_surface = pygame.transform.scale(comp_surface, new_size)
                                        
                                        # Left side - current image
                                        left_rect = img_rect.copy()
                                        left_rect.width = split_x
                                        screen.blit(img_surface, left_rect, (0, 0, split_x - left_rect.x, img_rect.height))
                                        
                                        # Right side - comparison image (same size as main image)
                                        right_rect = img_rect.copy()
                                        right_rect.x = split_x
                                        right_rect.width = width - split_x
                                        screen.blit(comp_surface, right_rect, (split_x - img_rect.x, 0, right_rect.width, img_rect.height))
                                        
                                        # Draw split line
                                        pygame.draw.line(screen, (255, 255, 255), (split_x, 0), (split_x, height), 2)
                                    else:
                                        # Fallback to single image if comparison image not available
                                        screen.blit(img_surface, img_rect)
                                else:
                                    # Single image mode
                                    screen.blit(img_surface, img_rect)
                                
                        except Exception as e:
                            print(f"Error displaying image: {e}")
                    
                    # Draw UI info
                    info_text = f"Image {current_idx + 1}/{len(images)} | Mode: {display_mode} | Zoom: {zoom:.2f}"
                    if display_mode == 'comparison':
                        if compare_images and len(compare_images) > 0:
                            info_text += " | Compare: External | Mouse to split"
                        elif len(images) > 1:
                            info_text += " | Compare: Next image | Mouse to split"
                        else:
                            info_text += " | No comparison image available"
                    elif display_mode == 'slideshow':
                        remaining_time = (slideshow_interval - slideshow_timer) / 1000
                        info_text += f" | Next: {remaining_time:.1f}s"
                    text_surface = font.render(info_text, True, (255, 255, 255))
                    screen.blit(text_surface, (10, 10))
                    
                    # Draw controls
                    controls = [
                        "ESC: Close | SPACE: Next Image | Ctrl+C: Comparison | Ctrl+S: Slideshow",
                        "Mouse Wheel: Zoom | Mouse Drag: Pan | Ctrl+R: Reset View"
                    ]
                    for i, control in enumerate(controls):
                        control_surface = font.render(control, True, (200, 200, 200))
                        screen.blit(control_surface, (10, height - 60 + i * 25))
                    
                    pygame.display.flip()
                    clock.tick(60)
                
                pygame.quit()
                
                # Clean up window data
                if monitor_idx in cls._windows:
                    del cls._windows[monitor_idx]
                
            except Exception as e:
                print(f"Enhanced Pygame error: {e}")
                # Clean up window data on error
                if monitor_idx in cls._windows:
                    del cls._windows[monitor_idx]
        
        # Create window data
        import threading
        
        
        # Stop existing window if any
        if monitor_idx in cls._windows:
            # Set visible to False to stop the existing window
            cls._windows[monitor_idx]["visible"] = False
            # Wait for the thread to finish
            import time
            if cls._windows[monitor_idx]["thread"].is_alive():
                cls._windows[monitor_idx]["thread"].join(timeout=2.0)
            else:
                print(f"  - Thread already finished")
            # Clean up the window data
            del cls._windows[monitor_idx]
        else:
            print(f"🔍 [DEBUG] No existing window for monitor {monitor_idx}")
        
        cls._windows[monitor_idx] = {
            "thread": threading.Thread(target=pygame_window_thread, daemon=True),
            "images": images,
            "compare_images": compare_images,
            "settings": settings,
            "lock": threading.Lock(),
            "visible": True,
            "running": True,
            "current_idx": 0,
            "refresh_needed": False
        }
        
        # Start window thread
        cls._windows[monitor_idx]["thread"].start()
    
    @classmethod
    def _get_monitor_info(cls, monitor_idx):
        """Get monitor information"""
        if PYGAME_AVAILABLE:
            try:
                num_displays = pygame.display.get_num_displays()
                if monitor_idx < num_displays:
                    info = pygame.display.Info(display=monitor_idx)
                    return {'width': info.current_w, 'height': info.current_h}
            except Exception:
                pass
        return {'width': 1920, 'height': 1080}
    
    @classmethod
    def _get_monitor_position(cls, monitor_idx):
        """Get monitor position for window placement"""
        # Try using screeninfo first (same as Pygame version)
        try:
            import screeninfo
            monitors = screeninfo.get_monitors()
            if monitor_idx < len(monitors):
                monitor = monitors[monitor_idx]
                print(f"Hybrid Preview Monitor: Monitor {monitor_idx} position from screeninfo: ({monitor.x}, {monitor.y})")
                return (monitor.x, monitor.y)
        except ImportError:
            print("Hybrid Preview Monitor: screeninfo not available")
        except Exception as e:
            print(f"Hybrid Preview Monitor: screeninfo error: {e}")
        
        # Fallback: estimate position based on monitor info
        if monitor_idx == 0:
            return (0, 0)
        else:
            # Get monitor info to estimate position
            monitor_info = cls._get_monitor_info(monitor_idx)
            width = monitor_info['width']
            # Assume monitors are side by side, but use actual width
            estimated_x = monitor_idx * width
            return (estimated_x, 0)
    
    @classmethod
    def cleanup_all_windows(cls):
        """Clean up all resources"""
        cls._image_cache.clear()
        if cls._server:
            cls._server.shutdown()
            cls._server = None

# ComfyUI node mappings
NODE_CLASS_MAPPINGS = {
    "HybridPreviewImageMonitor": HybridPreviewMonitor
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "HybridPreviewImageMonitor": "Hybrid Preview Image Monitor"
}

