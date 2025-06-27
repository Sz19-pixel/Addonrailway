import json
import re
import requests
from flask import Flask, jsonify, request
from bs4 import BeautifulSoup
import urllib.parse
from flask_cors import CORS
import logging
from datetime import datetime, timedelta
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import cloudscraper
import cfscrape
from fake_useragent import UserAgent
import threading
from urllib.parse import urljoin, urlparse
import base64

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Addon manifest
MANIFEST = {
    "id": "com.wecima.stremio",
    "version": "2.0.0",
    "name": "Wecima Pro Addon",
    "description": "Advanced streaming from Wecima.video with powerful scraping",
    "resources": ["catalog", "meta", "stream"],
    "types": ["movie", "series"],
    "catalogs": [
        {
            "type": "movie",
            "id": "wecima-movies",
            "name": "Wecima Movies",
            "extra": [{"name": "search", "isRequired": False}]
        },
        {
            "type": "series",
            "id": "wecima-series", 
            "name": "Wecima Series",
            "extra": [{"name": "search", "isRequired": False}]
        }
    ],
    "idPrefixes": ["wecima:"]
}

class AdvancedWecimaScraper:
    def __init__(self):
        self.base_url = "https://wecima.video"
        self.ua = UserAgent()
        
        # Initialize CloudScraper (bypasses Cloudflare)
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        self.scraper.headers.update({
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        })
        
        # Backup session
        self.session = requests.Session()
        self.session.headers.update(self.scraper.headers)
        
        # Chrome driver options (for Railway deployment)
        self.chrome_options = Options()
        self.chrome_options.add_argument('--headless')
        self.chrome_options.add_argument('--no-sandbox')
        self.chrome_options.add_argument('--disable-dev-shm-usage')
        self.chrome_options.add_argument('--disable-gpu')
        self.chrome_options.add_argument('--window-size=1920,1080')
        self.chrome_options.add_argument(f'--user-agent={self.ua.random}')
        
    def get_selenium_driver(self):
        """Get Selenium WebDriver for JavaScript-heavy pages"""
        try:
            driver = webdriver.Chrome(options=self.chrome_options)
            return driver
        except Exception as e:
            logger.error(f"Failed to create Chrome driver: {e}")
            return None
    
    def make_request(self, url, use_selenium=False, wait_for_element=None):
        """Make request with multiple fallback methods"""
        try:
            if use_selenium:
                driver = self.get_selenium_driver()
                if driver:
                    try:
                        driver.get(url)
                        if wait_for_element:
                            WebDriverWait(driver, 10).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_element))
                            )
                        content = driver.page_source
                        driver.quit()
                        return content
                    except Exception as e:
                        logger.error(f"Selenium error: {e}")
                        if driver:
                            driver.quit()
            
            # Try CloudScraper first
            try:
                response = self.scraper.get(url, timeout=15)
                response.raise_for_status()
                return response.text
            except Exception as e:
                logger.warning(f"CloudScraper failed: {e}")
            
            # Fallback to regular session
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.text
            
        except Exception as e:
            logger.error(f"All request methods failed for {url}: {e}")
            return None
    
    def search_content(self, query, content_type="movie"):
        """Advanced search with multiple methods"""
        try:
            # Method 1: Direct search
            search_url = f"{self.base_url}/search"
            search_data = {'s': query}
            
            # Try POST search
            try:
                response = self.scraper.post(search_url, data=search_data, timeout=10)
                if response.status_code == 200:
                    html_content = response.text
                else:
                    # Fallback to GET search
                    search_url = f"{self.base_url}/?s={urllib.parse.quote(query)}"
                    html_content = self.make_request(search_url)
            except:
                search_url = f"{self.base_url}/?s={urllib.parse.quote(query)}"
                html_content = self.make_request(search_url)
            
            if not html_content:
                return []
            
            soup = BeautifulSoup(html_content, 'html.parser')
            results = []
            
            # Multiple selectors for different page layouts
            selectors = [
                'article.post',
                'div.movie-item',
                'div.item',
                '.content-box',
                '.movie',
                '.film-item'
            ]
            
            content_items = []
            for selector in selectors:
                items = soup.select(selector)
                if items:
                    content_items = items
                    break
            
            for item in content_items[:15]:
                try:
                    # Find title with multiple selectors
                    title_selectors = ['h3 a', 'h2 a', '.title a', 'a.title', 'h4 a', '.movie-title a']
                    title_elem = None
                    link = None
                    
                    for sel in title_selectors:
                        title_elem = item.select_one(sel)
                        if title_elem:
                            link = title_elem.get('href')
                            break
                    
                    if not title_elem:
                        # Try any link in the item
                        link_elem = item.find('a')
                        if link_elem:
                            title_elem = link_elem
                            link = link_elem.get('href')
                    
                    if not title_elem or not link:
                        continue
                    
                    title = title_elem.get_text(strip=True)
                    if not link.startswith('http'):
                        link = urljoin(self.base_url, link)
                    
                    # Find poster with multiple selectors
                    poster_selectors = ['img.poster', '.poster img', 'img', '.movie-poster img']
                    poster = ""
                    for sel in poster_selectors:
                        img_elem = item.select_one(sel)
                        if img_elem:
                            poster = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-lazy-src')
                            if poster:
                                if not poster.startswith('http'):
                                    poster = urljoin(self.base_url, poster)
                                break
                    
                    # Extract year
                    year_match = re.search(r'(\d{4})', title)
                    year = year_match.group(1) if year_match else ""
                    
                    # Determine content type
                    detected_type = "movie"
                    if any(keyword in title.lower() for keyword in ['مسلسل', 'series', 'season', 'episode']):
                        detected_type = "series"
                    
                    results.append({
                        'id': f"wecima:{base64.b64encode(link.encode()).decode()}",
                        'title': title,
                        'year': year,
                        'poster': poster,
                        'link': link,
                        'type': detected_type
                    })
                    
                except Exception as e:
                    logger.error(f"Error parsing search result: {e}")
                    continue
            
            return results
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    def get_content_details(self, content_link):
        """Get detailed content information"""
        try:
            html_content = self.make_request(content_link, use_selenium=True, wait_for_element='.movie-details, .content')
            if not html_content:
                return None
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract title with multiple selectors
            title_selectors = ['h1.entry-title', 'h1', 'h2.title', '.movie-title h1', '.content-title']
            title = "Unknown"
            for sel in title_selectors:
                title_elem = soup.select_one(sel)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    break
            
            # Extract description
            desc_selectors = ['.story', '.description', '.movie-description', '.content p', '.synopsis']
            description = ""
            for sel in desc_selectors:
                desc_elem = soup.select_one(sel)
                if desc_elem:
                    description = desc_elem.get_text(strip=True)
                    break
            
            # Extract poster
            poster_selectors = ['.poster img', '.movie-poster img', '.content img', 'img.wp-post-image']
            poster = ""
            for sel in poster_selectors:
                poster_elem = soup.select_one(sel)
                if poster_elem:
                    poster = poster_elem.get('src') or poster_elem.get('data-src')
                    if poster and not poster.startswith('http'):
                        poster = urljoin(self.base_url, poster)
                    break
            
            # Extract additional metadata
            year_match = re.search(r'(\d{4})', title)
            year = year_match.group(1) if year_match else ""
            
            return {
                'title': title,
                'description': description,
                'poster': poster,
                'year': year
            }
            
        except Exception as e:
            logger.error(f"Error getting content details: {e}")
            return None
    
    def extract_streaming_links(self, content_link):
        """Advanced streaming link extraction with multiple methods"""
        try:
            # First, get the main page with Selenium for JavaScript content
            html_content = self.make_request(content_link, use_selenium=True, wait_for_element='video, iframe, .player')
            if not html_content:
                return []
            
            soup = BeautifulSoup(html_content, 'html.parser')
            streaming_links = []
            
            # Method 1: Direct video sources
            video_sources = soup.select('video source, video')
            for video in video_sources:
                src = video.get('src')
                if src and self.is_valid_stream_url(src):
                    if not src.startswith('http'):
                        src = urljoin(self.base_url, src)
                    
                    quality = self.extract_quality(src) or '720p'
                    streaming_links.append({
                        'url': src,
                        'quality': quality,
                        'title': f'Direct Stream ({quality})'
                    })
            
            # Method 2: Process iframes with Selenium
            iframes = soup.select('iframe')
            for iframe in iframes:
                src = iframe.get('src')
                if src:
                    if not src.startswith('http'):
                        src = urljoin(self.base_url, src)
                    
                    # Process iframe with Selenium
                    iframe_links = self.process_iframe(src)
                    streaming_links.extend(iframe_links)
            
            # Method 3: Extract from JavaScript
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    js_links = self.extract_from_javascript(script.string)
                    streaming_links.extend(js_links)
            
            # Method 4: Look for download/watch buttons and process them
            button_selectors = [
                'a[href*="watch"]', 'a[href*="play"]', 'a[href*="stream"]',
                '.watch-btn', '.play-btn', '.download-btn',
                'a:contains("مشاهدة")', 'a:contains("تحميل")'
            ]
            
            for selector in button_selectors:
                buttons = soup.select(selector)
                for button in buttons:
                    href = button.get('href')
                    if href:
                        if not href.startswith('http'):
                            href = urljoin(self.base_url, href)
                        
                        button_links = self.process_watch_button(href)
                        streaming_links.extend(button_links)
            
            # Method 5: Check for embed players
            embed_patterns = [
                r'(?:embed|player)\.php\?[^"\']*',
                r'vidsrc\.me/embed/[^"\']*',
                r'player\.php\?[^"\']*'
            ]
            
            page_text = str(soup)
            for pattern in embed_patterns:
                matches = re.findall(pattern, page_text)
                for match in matches:
                    if not match.startswith('http'):
                        match = urljoin(self.base_url, match)
                    
                    embed_links = self.process_embed_player(match)
                    streaming_links.extend(embed_links)
            
            # Remove duplicates and invalid links
            unique_links = []
            seen_urls = set()
            
            for link in streaming_links:
                if link['url'] not in seen_urls and self.is_valid_stream_url(link['url']):
                    seen_urls.add(link['url'])
                    unique_links.append(link)
            
            # Sort by quality preference
            quality_order = {'1080p': 1, '720p': 2, '480p': 3, '360p': 4}
            unique_links.sort(key=lambda x: quality_order.get(x['quality'], 5))
            
            return unique_links
            
        except Exception as e:
            logger.error(f"Error extracting streaming links: {e}")
            return []
    
    def process_iframe(self, iframe_url):
        """Process iframe content with Selenium"""
        try:
            iframe_content = self.make_request(iframe_url, use_selenium=True, wait_for_element='video, source')
            if not iframe_content:
                return []
            
            soup = BeautifulSoup(iframe_content, 'html.parser')
            links = []
            
            # Look for video sources in iframe
            video_sources = soup.select('video source, video')
            for video in video_sources:
                src = video.get('src')
                if src and self.is_valid_stream_url(src):
                    if not src.startswith('http'):
                        src = urljoin(iframe_url, src)
                    
                    quality = self.extract_quality(src) or '720p'
                    links.append({
                        'url': src,
                        'quality': quality,
                        'title': f'Iframe Stream ({quality})'
                    })
            
            return links
            
        except Exception as e:
            logger.error(f"Error processing iframe {iframe_url}: {e}")
            return []
    
    def extract_from_javascript(self, js_content):
        """Extract streaming URLs from JavaScript"""
        links = []
        
        # Pattern for video URLs in JavaScript
        patterns = [
            r'["\']https?://[^"\']*\.(?:mp4|m3u8|mkv|avi|mov)[^"\']*["\']',
            r'file\s*:\s*["\']([^"\']+)["\']',
            r'src\s*:\s*["\']([^"\']+)["\']',
            r'source\s*:\s*["\']([^"\']+)["\']'
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, js_content, re.IGNORECASE)
            for match in matches:
                url = match.strip('"\'')
                if self.is_valid_stream_url(url):
                    quality = self.extract_quality(url) or '720p'
                    links.append({
                        'url': url,
                        'quality': quality,
                        'title': f'JS Stream ({quality})'
                    })
        
        return links
    
    def process_watch_button(self, button_url):
        """Process watch/download button URLs"""
        try:
            content = self.make_request(button_url, use_selenium=True)
            if not content:
                return []
            
            soup = BeautifulSoup(content, 'html.parser')
            links = []
            
            # Look for video sources
            video_sources = soup.select('video source, video')
            for video in video_sources:
                src = video.get('src')
                if src and self.is_valid_stream_url(src):
                    if not src.startswith('http'):
                        src = urljoin(button_url, src)
                    
                    quality = self.extract_quality(src) or '720p'
                    links.append({
                        'url': src,
                        'quality': quality,
                        'title': f'Button Stream ({quality})'
                    })
            
            return links
            
        except Exception as e:
            logger.error(f"Error processing watch button {button_url}: {e}")
            return []
    
    def process_embed_player(self, embed_url):
        """Process embed player URLs"""
        try:
            content = self.make_request(embed_url, use_selenium=True, wait_for_element='video')
            if not content:
                return []
            
            soup = BeautifulSoup(content, 'html.parser')
            links = []
            
            # Look for video sources in embed player
            video_sources = soup.select('video source, video')
            for video in video_sources:
                src = video.get('src')
                if src and self.is_valid_stream_url(src):
                    if not src.startswith('http'):
                        src = urljoin(embed_url, src)
                    
                    quality = self.extract_quality(src) or '720p'
                    links.append({
                        'url': src,
                        'quality': quality,
                        'title': f'Embed Stream ({quality})'
                    })
            
            return links
            
        except Exception as e:
            logger.error(f"Error processing embed player {embed_url}: {e}")
            return []
    
    def is_valid_stream_url(self, url):
        """Check if URL is a valid streaming URL"""
        if not url or len(url) < 10:
            return False
        
        valid_extensions = ['.mp4', '.m3u8', '.mkv', '.avi', '.mov', '.webm', '.flv']
        valid_domains = ['wecima', 'cdn', 'stream', 'video', 'media']
        
        # Check for valid extensions
        if any(ext in url.lower() for ext in valid_extensions):
            return True
        
        # Check for streaming domains
        if any(domain in url.lower() for domain in valid_domains):
            return True
        
        # Check for common streaming patterns
        streaming_patterns = [
            r'stream',
            r'video',
            r'play',
            r'embed'
        ]
        
        return any(re.search(pattern, url, re.IGNORECASE) for pattern in streaming_patterns)
    
    def extract_quality(self, url):
        """Extract quality from URL"""
        quality_patterns = {
            '1080': '1080p',
            '720': '720p', 
            '480': '480p',
            '360': '360p'
        }
        
        for pattern, quality in quality_patterns.items():
            if pattern in url:
                return quality
        
        return None

# Initialize scraper
scraper = AdvancedWecimaScraper()

@app.route('/manifest.json')
def manifest():
    return jsonify(MANIFEST)

@app.route('/catalog/<catalog_type>/<catalog_id>.json')
def catalog(catalog_type, catalog_id):
    try:
        skip = int(request.args.get('skip', 0))
        search_query = request.args.get('search', '')
        
        if search_query:
            # Search request
            results = scraper.search_content(search_query, catalog_type)
        else:
            # Default catalog
            if catalog_type == "movie":
                results = scraper.search_content("2024 فيلم", "movie")
            else:
                results = scraper.search_content("مسلسل 2024", "series")
        
        catalog_items = []
        for item in results[skip:skip+20]:  # Pagination
            catalog_items.append({
                "id": item['id'],
                "type": catalog_type,
                "name": item['title'],
                "poster": item['poster'],
                "year": item['year']
            })
        
        return jsonify({"metas": catalog_items})
        
    except Exception as e:
        logger.error(f"Catalog error: {e}")
        return jsonify({"metas": []})

@app.route('/meta/<content_type>/<content_id>.json')
def meta(content_type, content_id):
    try:
        if content_id.startswith('wecima:'):
            # Decode base64 encoded URL
            encoded_url = content_id[7:]  # Remove 'wecima:' prefix
            content_link = base64.b64decode(encoded_url).decode()
            
            # Get content details
            details = scraper.get_content_details(content_link)
            
            if details:
                meta_data = {
                    "id": content_id,
                    "type": content_type,
                    "name": details['title'],
                    "description": details['description'],
                    "poster": details['poster'],
                    "year": details['year']
                }
                
                return jsonify({"meta": meta_data})
        
        return jsonify({"meta": {}})
        
    except Exception as e:
        logger.error(f"Meta error: {e}")
        return jsonify({"meta": {}})

@app.route('/stream/<content_type>/<content_id>.json')
def stream(content_type, content_id):
    try:
        if content_id.startswith('wecima:'):
            # Decode base64 encoded URL
            encoded_url = content_id[7:]  # Remove 'wecima:' prefix
            content_link = base64.b64decode(encoded_url).decode()
            
            # Extract streaming links
            streaming_links = scraper.extract_streaming_links(content_link)
            
            streams = []
            for link in streaming_links:
                # Add subtitle support
                stream_data = {
                    "url": link['url'],
                    "quality": link['quality'],
                    "title": f"🎬 Wecima - {link['title']}",
                    "tag": [link['quality']],
                }
                
                # Add headers for some streams
                if 'wecima' in link['url'].lower():
                    stream_data["behaviorHints"] = {
                        "notWebReady": True
                    }
                
                streams.append(stream_data)
            
            logger.info(f"Found {len(streams)} streams for {content_id}")
            return jsonify({"streams": streams})
        
        return jsonify({"streams": []})
        
    except Exception as e:
        logger.error(f"Stream error: {e}")
        return jsonify({"streams": []})

@app.route('/search/<query>')
def search_endpoint(query):
    """Search endpoint for testing"""
    try:
        results = scraper.search_content(query)
        return jsonify({"results": results})
    except Exception as e:
        logger.error(f"Search endpoint error: {e}")
        return jsonify({"results": []})

@app.route('/health')
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

@app.route('/')
def index():
    return jsonify({
        "name": "Wecima Pro Stremio Addon",
        "version": "2.0.0",
        "description": "Advanced streaming from Wecima.video with powerful scraping",
        "manifest": "/manifest.json",
        "endpoints": {
            "manifest": "/manifest.json",
            "catalog": "/catalog/{type}/{id}.json",
            "meta": "/meta/{type}/{id}.json", 
            "stream": "/stream/{type}/{id}.json",
            "search": "/search/{query}",
            "health": "/health"
        }
    })

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 7000))
    app.run(host='0.0.0.0', port=port, debug=False)
