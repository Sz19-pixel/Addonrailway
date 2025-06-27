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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Addon manifest
MANIFEST = {
    "id": "com.wecima.stremio",
    "version": "1.0.0",
    "name": "Wecima Addon",
    "description": "Stream movies and TV shows from Wecima.video",
    "resources": ["catalog", "meta", "stream"],
    "types": ["movie", "series"],
    "catalogs": [
        {
            "type": "movie",
            "id": "wecima-movies",
            "name": "Wecima Movies"
        },
        {
            "type": "series",
            "id": "wecima-series", 
            "name": "Wecima Series"
        }
    ],
    "idPrefixes": ["wecima:"]
}

class WecimaScraper:
    def __init__(self):
        self.base_url = "https://wecima.video"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
    
    def search_content(self, query, content_type="movie"):
        """Search for content on Wecima"""
        try:
            search_url = f"{self.base_url}/search/{urllib.parse.quote(query)}"
            response = self.session.get(search_url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            results = []
            
            # Find movie/series containers
            content_items = soup.find_all('div', class_='item')
            
            for item in content_items[:10]:  # Limit to 10 results
                try:
                    title_elem = item.find('h3') or item.find('a')
                    link_elem = item.find('a')
                    img_elem = item.find('img')
                    
                    if title_elem and link_elem:
                        title = title_elem.get_text(strip=True)
                        link = link_elem.get('href')
                        if not link.startswith('http'):
                            link = self.base_url + link
                        
                        poster = ""
                        if img_elem:
                            poster = img_elem.get('src') or img_elem.get('data-src')
                            if poster and not poster.startswith('http'):
                                poster = self.base_url + poster
                        
                        # Extract year from title if possible
                        year_match = re.search(r'\((\d{4})\)', title)
                        year = year_match.group(1) if year_match else ""
                        
                        results.append({
                            'id': f"wecima:{urllib.parse.quote(link)}",
                            'title': title,
                            'year': year,
                            'poster': poster,
                            'link': link
                        })
                except Exception as e:
                    logger.error(f"Error parsing search result: {e}")
                    continue
            
            return results
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    def get_movie_details(self, movie_link):
        """Get movie details from Wecima"""
        try:
            response = self.session.get(movie_link, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract movie details
            title_elem = soup.find('h1') or soup.find('h2', class_='title')
            title = title_elem.get_text(strip=True) if title_elem else "Unknown"
            
            # Extract description
            desc_elem = soup.find('div', class_='story') or soup.find('div', class_='description')
            description = desc_elem.get_text(strip=True) if desc_elem else ""
            
            # Extract poster
            poster_elem = soup.find('img', class_='poster') or soup.find('div', class_='poster').find('img') if soup.find('div', class_='poster') else None
            poster = ""
            if poster_elem:
                poster = poster_elem.get('src') or poster_elem.get('data-src')
                if poster and not poster.startswith('http'):
                    poster = self.base_url + poster
            
            return {
                'title': title,
                'description': description,
                'poster': poster
            }
        except Exception as e:
            logger.error(f"Error getting movie details: {e}")
            return None
    
    def extract_streaming_links(self, content_link):
        """Extract streaming links from content page"""
        try:
            response = self.session.get(content_link, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            streaming_links = []
            
            # Look for various streaming link patterns
            # Method 1: Direct video links
            video_links = soup.find_all('source') + soup.find_all('video')
            for video in video_links:
                src = video.get('src')
                if src and any(ext in src.lower() for ext in ['.mp4', '.m3u8', '.mkv']):
                    if not src.startswith('http'):
                        src = self.base_url + src
                    streaming_links.append({
                        'url': src,
                        'quality': '720p',
                        'title': 'Direct Stream'
                    })
            
            # Method 2: Embedded iframe sources
            iframes = soup.find_all('iframe')
            for iframe in iframes:
                src = iframe.get('src')
                if src:
                    if not src.startswith('http'):
                        src = self.base_url + src
                    
                    # Try to extract from iframe
                    try:
                        iframe_response = self.session.get(src, timeout=5)
                        iframe_soup = BeautifulSoup(iframe_response.text, 'html.parser')
                        
                        # Look for video sources in iframe
                        iframe_videos = iframe_soup.find_all('source') + iframe_soup.find_all('video')
                        for video in iframe_videos:
                            video_src = video.get('src')
                            if video_src and any(ext in video_src.lower() for ext in ['.mp4', '.m3u8', '.mkv']):
                                if not video_src.startswith('http'):
                                    video_src = self.base_url + video_src
                                streaming_links.append({
                                    'url': video_src,
                                    'quality': '720p',
                                    'title': 'Embedded Stream'
                                })
                    except:
                        pass
            
            # Method 3: JavaScript embedded links
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    # Look for video URLs in JavaScript
                    video_urls = re.findall(r'["\']https?://[^"\']*\.(?:mp4|m3u8|mkv)[^"\']*["\']', script.string)
                    for url in video_urls:
                        clean_url = url.strip('"\'')
                        streaming_links.append({
                            'url': clean_url,
                            'quality': '720p',
                            'title': 'JS Stream'
                        })
            
            # Method 4: Look for download/watch buttons
            watch_buttons = soup.find_all('a', string=re.compile(r'(watch|مشاهدة|تحميل)', re.I))
            for button in watch_buttons:
                href = button.get('href')
                if href:
                    if not href.startswith('http'):
                        href = self.base_url + href
                    
                    # Try to get the actual video URL
                    try:
                        button_response = self.session.get(href, timeout=5)
                        button_soup = BeautifulSoup(button_response.text, 'html.parser')
                        
                        # Look for video sources
                        button_videos = button_soup.find_all('source') + button_soup.find_all('video')
                        for video in button_videos:
                            video_src = video.get('src')
                            if video_src and any(ext in video_src.lower() for ext in ['.mp4', '.m3u8', '.mkv']):
                                if not video_src.startswith('http'):
                                    video_src = self.base_url + video_src
                                streaming_links.append({
                                    'url': video_src,
                                    'quality': '720p',
                                    'title': 'Watch Button'
                                })
                    except:
                        pass
            
            # Remove duplicates
            unique_links = []
            seen_urls = set()
            for link in streaming_links:
                if link['url'] not in seen_urls:
                    seen_urls.add(link['url'])
                    unique_links.append(link)
            
            return unique_links
            
        except Exception as e:
            logger.error(f"Error extracting streaming links: {e}")
            return []

# Initialize scraper
scraper = WecimaScraper()

@app.route('/manifest.json')
def manifest():
    return jsonify(MANIFEST)

@app.route('/catalog/<catalog_type>/<catalog_id>.json')
def catalog(catalog_type, catalog_id):
    try:
        # Get popular/trending content
        if catalog_type == "movie":
            # Search for popular movies
            results = scraper.search_content("2024", "movie")
            
            catalog_items = []
            for item in results:
                catalog_items.append({
                    "id": item['id'],
                    "type": "movie",
                    "name": item['title'],
                    "poster": item['poster'],
                    "year": item['year']
                })
            
            return jsonify({"metas": catalog_items})
        
        elif catalog_type == "series":
            # Search for popular series
            results = scraper.search_content("مسلسل", "series")
            
            catalog_items = []
            for item in results:
                catalog_items.append({
                    "id": item['id'],
                    "type": "series", 
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
        # Decode the content ID to get the original link
        if content_id.startswith('wecima:'):
            content_link = urllib.parse.unquote(content_id[7:])  # Remove 'wecima:' prefix
            
            # Get movie/series details
            details = scraper.get_movie_details(content_link)
            
            if details:
                meta_data = {
                    "id": content_id,
                    "type": content_type,
                    "name": details['title'],
                    "description": details['description'],
                    "poster": details['poster']
                }
                
                return jsonify({"meta": meta_data})
        
        return jsonify({"meta": {}})
        
    except Exception as e:
        logger.error(f"Meta error: {e}")
        return jsonify({"meta": {}})

@app.route('/stream/<content_type>/<content_id>.json')
def stream(content_type, content_id):
    try:
        # Decode the content ID to get the original link
        if content_id.startswith('wecima:'):
            content_link = urllib.parse.unquote(content_id[7:])  # Remove 'wecima:' prefix
            
            # Extract streaming links
            streaming_links = scraper.extract_streaming_links(content_link)
            
            streams = []
            for link in streaming_links:
                stream_data = {
                    "url": link['url'],
                    "quality": link['quality'],
                    "title": f"Wecima - {link['title']} ({link['quality']})"
                }
                streams.append(stream_data)
            
            return jsonify({"streams": streams})
        
        return jsonify({"streams": []})
        
    except Exception as e:
        logger.error(f"Stream error: {e}")
        return jsonify({"streams": []})

@app.route('/search/<query>')
def search(query):
    """Search endpoint for testing"""
    try:
        results = scraper.search_content(query)
        return jsonify({"results": results})
    except Exception as e:
        logger.error(f"Search endpoint error: {e}")
        return jsonify({"results": []})

@app.route('/')
def index():
    return jsonify({
        "name": "Wecima Stremio Addon",
        "version": "1.0.0",
        "description": "Stream content from Wecima.video",
        "manifest": "/manifest.json"
    })

if __name__ == '__main__':
    # For Railway deployment
    import os
    port = int(os.environ.get('PORT', 7000))
    app.run(host='0.0.0.0', port=port, debug=False)
