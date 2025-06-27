from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
import json
import os
import logging
from urllib.parse import urljoin, urlparse, parse_qs
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import cloudscraper
import base64

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Enhanced headers with more realistic browser fingerprinting
def get_random_headers():
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15'
    ]
    
    return {
        'User-Agent': random.choice(user_agents),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
        'DNT': '1',
        'Sec-CH-UA': '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"Windows"'
    }

# Addon manifest
MANIFEST = {
    "id": "community.vidfast.scraper.addon.v2",
    "version": "2.0.0",
    "name": "VidFast Enhanced Scraper",
    "description": "Enhanced scraper for VidFast with Cloudflare bypass and improved stream detection",
    "logo": "https://via.placeholder.com/256x256/FF6B35/FFFFFF?text=VFS",
    "background": "https://via.placeholder.com/1280x720/1a1a1a/FFFFFF?text=VidFast+Enhanced",
    
    "resources": ["stream"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt", "tmdb:"],
    
    "catalogs": []
}

class EnhancedVidFastScraper:
    def __init__(self):
        # Use cloudscraper to handle Cloudflare protection
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'mobile': False
            },
            delay=1,
            debug=False
        )
        
        # Backup session for non-Cloudflare requests
        self.session = requests.Session()
        
        # Possible base URLs to try
        self.base_urls = [
            "https://vidfast.pro",
            "https://www.vidfast.pro",
            "https://vidfast.to",
            "https://vidfast.net"
        ]
        
        self.working_base_url = None
        self.find_working_base_url()
    
    def find_working_base_url(self):
        """Find the working base URL"""
        for url in self.base_urls:
            try:
                response = self.scraper.get(url, timeout=10)
                if response.status_code == 200:
                    self.working_base_url = url
                    logger.info(f"Found working base URL: {url}")
                    return
            except Exception as e:
                logger.debug(f"Failed to connect to {url}: {e}")
                continue
        
        # If all fail, use the first one as fallback
        self.working_base_url = self.base_urls[0]
        logger.warning(f"No working base URL found, using fallback: {self.working_base_url}")
    
    def extract_id(self, id_str):
        """Extract clean ID from different formats"""
        if id_str.startswith('tmdb:'):
            return id_str.replace('tmdb:', '')
        return id_str
    
    def get_page_content(self, url, use_cloudscraper=True):
        """Fetch page content with enhanced error handling and Cloudflare bypass"""
        headers = get_random_headers()
        
        try:
            if use_cloudscraper:
                response = self.scraper.get(url, headers=headers, timeout=15)
            else:
                self.session.headers.update(headers)
                response = self.session.get(url, timeout=15)
            
            response.raise_for_status()
            return response.text
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            
            # Try with backup method
            if use_cloudscraper:
                logger.info("Retrying with regular session...")
                return self.get_page_content(url, use_cloudscraper=False)
            
            return None
    
    def extract_video_sources(self, html_content, page_url):
        """Enhanced video source extraction with multiple patterns"""
        soup = BeautifulSoup(html_content, 'html.parser')
        sources = []
        
        # Enhanced patterns for modern streaming sites
        video_patterns = [
            # Standard video/source tags
            r'<video[^>]*src=["\']([^"\']+)["\']',
            r'<source[^>]*src=["\']([^"\']+)["\']',
            
            # JavaScript video configurations
            r'src\s*:\s*["\']([^"\']+\.(?:m3u8|mp4|webm|avi|mkv|mov|flv|ts))["\']',
            r'file\s*:\s*["\']([^"\']+\.(?:m3u8|mp4|webm|avi|mkv|mov|flv|ts))["\']',
            r'video\s*:\s*["\']([^"\']+\.(?:m3u8|mp4|webm|avi|mkv|mov|flv|ts))["\']',
            r'url\s*:\s*["\']([^"\']+\.(?:m3u8|mp4|webm|avi|mkv|mov|flv|ts))["\']',
            r'link\s*:\s*["\']([^"\']+\.(?:m3u8|mp4|webm|avi|mkv|mov|flv|ts))["\']',
            
            # HLS and DASH streams
            r'["\']([^"\']+\.m3u8(?:\?[^"\']*)?)["\']',
            r'["\']([^"\']+\.mpd(?:\?[^"\']*)?)["\']',
            
            # MP4 and other video formats
            r'["\']([^"\']+\.mp4(?:\?[^"\']*)?)["\']',
            r'["\']([^"\']+\.webm(?:\?[^"\']*)?)["\']',
            r'["\']([^"\']+\.avi(?:\?[^"\']*)?)["\']',
            r'["\']([^"\']+\.mkv(?:\?[^"\']*)?)["\']',
            
            # Streaming URLs with common patterns
            r'["\']([^"\']*(?:stream|video|play|embed|player)[^"\']*\.(?:m3u8|mp4|webm|ts)(?:\?[^"\']*)?)["\']',
            
            # Base64 encoded sources
            r'atob\s*\(\s*["\']([^"\']+)["\']',
            
            # Common streaming parameters
            r'playlist\s*:\s*["\']([^"\']+)["\']',
            r'manifest\s*:\s*["\']([^"\']+)["\']',
        ]
        
        for pattern in video_patterns:
            matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
            for match in matches:
                # Handle base64 encoded URLs
                if pattern.startswith('atob'):
                    try:
                        decoded = base64.b64decode(match).decode('utf-8')
                        if self.is_valid_video_url(decoded):
                            sources.append(decoded)
                    except:
                        continue
                elif self.is_valid_video_url(match):
                    sources.append(match)
        
        # Extract iframe sources with enhanced detection
        iframes = soup.find_all('iframe')
        for iframe in iframes:
            src = iframe.get('src') or iframe.get('data-src')
            if src and self.is_streaming_iframe(src):
                # Make URL absolute
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    src = urljoin(page_url, src)
                
                # Try to extract sources from iframe
                iframe_sources = self.scrape_iframe_sources(src)
                sources.extend(iframe_sources)
        
        # Look for encrypted or obfuscated sources
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string:
                # Look for common obfuscation patterns
                obfuscated_sources = self.extract_obfuscated_sources(script.string)
                sources.extend(obfuscated_sources)
        
        # Remove duplicates and make URLs absolute
        clean_sources = []
        for source in set(sources):
            if source.startswith('//'):
                source = 'https:' + source
            elif source.startswith('/'):
                source = urljoin(page_url, source)
            
            if self.is_valid_video_url(source) and source not in clean_sources:
                clean_sources.append(source)
        
        return self.sort_sources_by_quality(clean_sources)
    
    def extract_obfuscated_sources(self, script_content):
        """Extract sources from obfuscated JavaScript"""
        sources = []
        
        # Common obfuscation patterns
        patterns = [
            # Hex encoded strings
            r'\\x([0-9a-fA-F]{2})',
            # Unicode escapes
            r'\\u([0-9a-fA-F]{4})',
            # Simple string concatenation
            r'["\']([^"\']*)["\'][\s\+]*["\']([^"\']*\.(?:m3u8|mp4|webm))["\']',
        ]
        
        # Try to decode hex/unicode
        try:
            decoded_script = script_content.encode().decode('unicode_escape')
            # Look for video URLs in decoded content
            video_urls = re.findall(r'["\']([^"\']+\.(?:m3u8|mp4|webm|ts)(?:\?[^"\']*)?)["\']', decoded_script)
            for url in video_urls:
                if self.is_valid_video_url(url):
                    sources.append(url)
        except:
            pass
        
        return sources
    
    def is_valid_video_url(self, url):
        """Enhanced video URL validation"""
        if not url or len(url) < 10:
            return False
        
        # Skip invalid schemes
        if url.startswith(('data:', 'javascript:', 'about:', 'mailto:')):
            return False
        
        # Skip obvious non-video URLs
        if any(x in url.lower() for x in ['font', 'css', 'js', 'json', 'xml', 'txt', 'png', 'jpg', 'jpeg', 'gif', 'svg', 'ico']):
            return False
        
        # Check for video file extensions
        video_extensions = ['.m3u8', '.mp4', '.webm', '.avi', '.mkv', '.mov', '.flv', '.ts', '.mpd']
        url_lower = url.lower()
        
        if any(ext in url_lower for ext in video_extensions):
            return True
        
        # Check for streaming indicators
        streaming_indicators = ['stream', 'video', 'play', 'embed', 'player', 'watch', 'media']
        if any(indicator in url_lower for indicator in streaming_indicators):
            # Additional checks for streaming URLs
            if any(x in url_lower for x in ['hls', 'dash', 'manifest', 'playlist']):
                return True
            
            # Check if it's a proper streaming domain
            parsed = urlparse(url)
            if parsed.netloc and len(parsed.path) > 1:
                return True
        
        return False
    
    def is_streaming_iframe(self, url):
        """Enhanced iframe detection for video players"""
        streaming_indicators = [
            'vidfast', 'embed', 'player', 'stream', 'video', 'watch',
            'vidsrc', 'vidcloud', 'upstream', 'fembed', 'streamtape',
            'doodstream', 'streamlare', 'mixdrop', 'mp4upload',
            'videovard', 'streamhub', 'vidoza', 'speedostream'
        ]
        
        url_lower = url.lower()
        return any(indicator in url_lower for indicator in streaming_indicators)
    
    def scrape_iframe_sources(self, iframe_url):
        """Enhanced iframe scraping with retry logic"""
        try:
            logger.info(f"Scraping iframe: {iframe_url}")
            
            # Add delay to avoid rate limiting
            time.sleep(random.uniform(1, 3))
            
            content = self.get_page_content(iframe_url)
            if content:
                return self.extract_video_sources(content, iframe_url)
        except Exception as e:
            logger.error(f"Failed to scrape iframe {iframe_url}: {e}")
        
        return []
    
    def sort_sources_by_quality(self, sources):
        """Enhanced quality sorting"""
        quality_scores = {
            '4k': 10, '2160p': 10, '1440p': 8, '1080p': 6,
            '720p': 4, '480p': 2, '360p': 1, '240p': 0
        }
        
        def get_quality_score(url):
            url_lower = url.lower()
            
            # Check for explicit quality indicators
            for quality, score in quality_scores.items():
                if quality in url_lower:
                    return score
            
            # Prefer streaming formats
            if '.m3u8' in url_lower:
                return 7  # HLS adaptive streaming
            elif '.mpd' in url_lower:
                return 7  # DASH adaptive streaming
            elif '.mp4' in url_lower:
                return 5  # MP4 direct
            elif '.webm' in url_lower:
                return 4  # WebM
            elif '.ts' in url_lower:
                return 3  # Transport Stream
            
            # Prefer URLs with streaming indicators
            if any(x in url_lower for x in ['hd', 'high', 'best', 'premium']):
                return 6
            
            return 3  # Default score
        
        return sorted(sources, key=get_quality_score, reverse=True)
    
    def get_quality_label(self, url):
        """Enhanced quality label detection"""
        url_lower = url.lower()
        
        quality_map = {
            '4k': '4K', '2160p': '4K', '1440p': '1440p', '1080p': '1080p',
            '720p': '720p', '480p': '480p', '360p': '360p', '240p': '240p'
        }
        
        for quality, label in quality_map.items():
            if quality in url_lower:
                return label
        
        # Check file extension
        if '.m3u8' in url_lower:
            return 'HLS'
        elif '.mpd' in url_lower:
            return 'DASH'
        elif '.mp4' in url_lower:
            return 'MP4'
        elif '.webm' in url_lower:
            return 'WebM'
        elif '.ts' in url_lower:
            return 'TS'
        
        # Check for quality indicators
        if any(x in url_lower for x in ['hd', 'high', 'premium']):
            return 'HD'
        elif any(x in url_lower for x in ['sd', 'standard']):
            return 'SD'
        
        return 'Stream'
    
    def scrape_movie(self, movie_id):
        """Enhanced movie scraping with multiple attempts"""
        movie_urls = [
            f"{self.working_base_url}/movie/{movie_id}",
            f"{self.working_base_url}/watch/{movie_id}",
            f"{self.working_base_url}/film/{movie_id}",
            f"{self.working_base_url}/m/{movie_id}"
        ]
        
        for movie_url in movie_urls:
            logger.info(f"Trying movie URL: {movie_url}")
            
            content = self.get_page_content(movie_url)
            if content:
                sources = self.extract_video_sources(content, movie_url)
                
                if sources:
                    streams = []
                    for i, source in enumerate(sources[:5]):  # Limit to top 5
                        quality = self.get_quality_label(source)
                        
                        streams.append({
                            "title": f"🎬 VidFast Enhanced - {quality} (Source {i+1})",
                            "url": source,
                            "behaviorHints": {
                                "notWebReady": False,
                                "bingeGroup": f"vidfast-movie-{movie_id}",
                                "countryWhitelist": ["US", "GB", "CA", "AU", "DE", "FR", "IT", "ES", "NL", "BE"]
                            }
                        })
                    
                    return streams
        
        # Fallback with direct link
        fallback_url = f"{self.working_base_url}/movie/{movie_id}?autoPlay=true"
        return [{
            "title": "🎬 VidFast Enhanced - Direct Link",
            "url": fallback_url,
            "behaviorHints": {
                "notWebReady": False,
                "bingeGroup": f"vidfast-movie-{movie_id}"
            }
        }]
    
    def scrape_tv_episode(self, series_id, season, episode):
        """Enhanced TV episode scraping"""
        tv_urls = [
            f"{self.working_base_url}/tv/{series_id}/{season}/{episode}",
            f"{self.working_base_url}/series/{series_id}/{season}/{episode}",
            f"{self.working_base_url}/watch/{series_id}/{season}/{episode}",
            f"{self.working_base_url}/s/{series_id}/{season}/{episode}"
        ]
        
        for tv_url in tv_urls:
            logger.info(f"Trying TV URL: {tv_url}")
            
            content = self.get_page_content(tv_url)
            if content:
                sources = self.extract_video_sources(content, tv_url)
                
                if sources:
                    streams = []
                    for i, source in enumerate(sources[:5]):
                        quality = self.get_quality_label(source)
                        
                        streams.append({
                            "title": f"📺 VidFast Enhanced - S{season.zfill(2)}E{episode.zfill(2)} {quality} (Source {i+1})",
                            "url": source,
                            "behaviorHints": {
                                "notWebReady": False,
                                "bingeGroup": f"vidfast-series-{series_id}",
                                "countryWhitelist": ["US", "GB", "CA", "AU", "DE", "FR", "IT", "ES", "NL", "BE"]
                            }
                        })
                    
                    return streams
        
        # Fallback
        fallback_url = f"{self.working_base_url}/tv/{series_id}/{season}/{episode}?autoPlay=true"
        return [{
            "title": f"📺 VidFast Enhanced - S{season.zfill(2)}E{episode.zfill(2)} Direct Link",
            "url": fallback_url,
            "behaviorHints": {
                "notWebReady": False,
                "bingeGroup": f"vidfast-series-{series_id}"
            }
        }]

# Initialize enhanced scraper
scraper = EnhancedVidFastScraper()

@app.route('/')pp.route('/')
def home():
    return send_file("landing.html")

@app.route('/')
def home():
    """Home route with addon info"""
    return jsonify({
        "addon": MANIFEST["name"],
        "version": MANIFEST["version"],
        "description": MANIFEST["description"],
        "manifest_url": f"{request.host_url}manifest.json",
        "working_base_url": scraper.working_base_url,
        "status": "Enhanced with Cloudflare bypass and improved detection"
    })

@app.route('/manifest.json')
def addon_manifest():
    """Return the addon manifest"""
    return jsonify(MANIFEST)

@app.route('/stream/<type>/<id>.json')
def addon_stream(type, id):
    """Enhanced stream handler with better error handling"""
    try:
        logger.info(f"Enhanced stream request - Type: {type}, ID: {id}")
        
        clean_id = scraper.extract_id(id)
        streams = []
        
        if type == 'movie':
            streams = scraper.scrape_movie(clean_id)
        elif type == 'series':
            parts = id.split(':')
            if len(parts) >= 3:
                series_id = scraper.extract_id(parts[0])
                season = parts[1]
                episode = parts[2]
                streams = scraper.scrape_tv_episode(series_id, season, episode)
            else:
                logger.error(f"Invalid series ID format: {id}")
                return jsonify({"streams": []})
        
        logger.info(f"Found {len(streams)} streams for {type} {id}")
        return jsonify({"streams": streams})
        
    except Exception as e:
        logger.error(f"Error in enhanced stream handler: {e}")
        return jsonify({"streams": []})

@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "working_base_url": scraper.working_base_url,
        "timestamp": time.time()
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
