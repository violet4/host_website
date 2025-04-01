#!/usr/bin/env python3

import argparse
import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin

import uvicorn
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

class DomainRewriter:
    def __init__(self, original_domain, content_root):
        self.original_domain = original_domain
        if not self.original_domain.startswith(('http://', 'https://')):
            self.original_domain = f'https://{self.original_domain}'

        self.domain_url = urlparse(self.original_domain)
        self.content_root = Path(content_root).resolve()

        # Patterns to find URLs in various contexts
        self.url_patterns = [
            # Handle various URL formats
            (re.compile(f'(https?://{self.domain_url.netloc})(/[^"\'\\s]*)?', re.IGNORECASE), self._replace_full_url),
            (re.compile(f'"//(.*?{self.domain_url.netloc})(/[^"\'\\s]*)?', re.IGNORECASE), self._replace_protocol_relative_url),
            (re.compile(f'\'//(.*?{self.domain_url.netloc})(/[^"\'\\s]*)?', re.IGNORECASE), self._replace_protocol_relative_url),
        ]

    def _replace_full_url(self, match):
        return "" if match.group(2) is None else match.group(2)

    def _replace_protocol_relative_url(self, match):
        if match.group(2) is None:
            return '""' if match.group(0).startswith('"') else "''"
        return f'"{match.group(2)}"' if match.group(0).startswith('"') else f"'{match.group(2)}'"

    def rewrite_html(self, content, request_base_url):
        """Rewrite URLs in HTML using BeautifulSoup for more precise replacements"""
        soup = BeautifulSoup(content, 'html.parser')

        # Handle links (href attributes)
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if href.startswith(self.original_domain):
                a_tag['href'] = href.replace(self.original_domain, '')
            elif href.startswith(f'//{self.domain_url.netloc}'):
                a_tag['href'] = href.replace(f'//{self.domain_url.netloc}', '')

        # Handle scripts (src attributes)
        for script_tag in soup.find_all('script', src=True):
            src = script_tag['src']
            if src.startswith(self.original_domain):
                script_tag['src'] = src.replace(self.original_domain, '')
            elif src.startswith(f'//{self.domain_url.netloc}'):
                script_tag['src'] = src.replace(f'//{self.domain_url.netloc}', '')

        # Handle images (src attributes)
        for img_tag in soup.find_all('img', src=True):
            src = img_tag['src']
            if src.startswith(self.original_domain):
                img_tag['src'] = src.replace(self.original_domain, '')
            elif src.startswith(f'//{self.domain_url.netloc}'):
                img_tag['src'] = src.replace(f'//{self.domain_url.netloc}', '')

        # Handle links (href attributes)
        for link_tag in soup.find_all('link', href=True):
            href = link_tag['href']
            if href.startswith(self.original_domain):
                link_tag['href'] = href.replace(self.original_domain, '')
            elif href.startswith(f'//{self.domain_url.netloc}'):
                link_tag['href'] = href.replace(f'//{self.domain_url.netloc}', '')

        # Handle meta refresh and other URL references
        for meta_tag in soup.find_all('meta', attrs={'content': True}):
            if 'http-equiv' in meta_tag.attrs and meta_tag['http-equiv'].lower() == 'refresh':
                content = meta_tag['content']
                if self.original_domain in content:
                    meta_tag['content'] = content.replace(self.original_domain, '')
                elif f'//{self.domain_url.netloc}' in content:
                    meta_tag['content'] = content.replace(f'//{self.domain_url.netloc}', '')


        # Handle inline styles with urls
        for tag in soup.find_all(style=True):
            style = tag['style']
            if self.original_domain in style:
                tag['style'] = style.replace(self.original_domain, '')
            elif f'//{self.domain_url.netloc}' in style:
                tag['style'] = style.replace(f'//{self.domain_url.netloc}', '')

        # Handle data attributes that might contain URLs
        for tag in soup.find_all(True):
            for attr_name, attr_value in tag.attrs.items():
                if isinstance(attr_value, str) and attr_name.startswith('data-') and (self.original_domain in attr_value or f'//{self.domain_url.netloc}' in attr_value):
                    tag[attr_name] = attr_value.replace(self.original_domain, '').replace(f'//{self.domain_url.netloc}', '')

        # Handle form actions
        for form_tag in soup.find_all('form', action=True):
            action = form_tag['action']
            if action.startswith(self.original_domain):
                form_tag['action'] = action.replace(self.original_domain, '')
            elif action.startswith(f'//{self.domain_url.netloc}'):
                form_tag['action'] = action.replace(f'//{self.domain_url.netloc}', '')

        # Add base tag if it doesn't exist to ensure relative paths work correctly
        head = soup.find('head')
        if head and not soup.find('base'):
            base_tag = soup.new_tag('base')
            base_tag['href'] = request_base_url
            head.insert(0, base_tag)

        content = str(soup)
        netloc = self.domain_url.netloc
        while netloc in content:
            print('netloc in content!')
            content = re.sub(f"(https?://)?{netloc}", "", content)

        return content

    def rewrite_css(self, content):
        """Rewrite URLs in CSS files"""
        for pattern, replacer in self.url_patterns:
            content = pattern.sub(replacer, content)
        return content

    def rewrite_js(self, content):
        """Rewrite URLs in JavaScript files"""
        for pattern, replacer in self.url_patterns:
            content = pattern.sub(replacer, content)
        return content

    def get_file_path(self, path):
        """Convert URL path to file system path"""
        raw_path = self.content_root / path.lstrip('/')
        if raw_path.exists() and raw_path.is_file():
            return raw_path
        if path == '/':
            path = '/index.html'
        elif not path.endswith(('.html', '.htm', '.css', '.js', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.ico', '.pdf', '.txt')):
            path = f"{path}/index.html" if not path.endswith('/') else f"{path}index.html"

        file_path = self.content_root / path.lstrip('/')
        return file_path if file_path.exists() else None

# Global variable to store the rewriter instance
for k in ('DOMAIN', 'DIRECTORY'):
    print(k, os.environ.get(k))
rewriter = DomainRewriter(os.environ.get('DOMAIN'), os.environ.get('DIRECTORY'))

@app.get("/{full_path:path}")
async def serve_content(full_path: str, request: Request):
    global rewriter
    path = f"/{full_path}"
    suffix = Path(path).suffix
    if request.url.query:
        path += '?' + request.url.query

    file_path = rewriter.get_file_path(path)
    if not file_path:
        return Response(content=f"File not found: {path}", status_code=404)

    new_suffix = Path(file_path).suffix
    if new_suffix and not suffix:
        suffix = new_suffix

    if not suffix:
        if file_path.is_file():
            with open(file_path, 'r') as fr:
                if fr.read(100).lstrip().startswith('<!DOCTYPE html>'):
                    suffix = '.html'

    content_type = None
    if suffix == '.html' or suffix == '.htm':
        content_type = 'text/html'
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Get base URL for the request
        base_url = str(request.base_url)
        if not base_url.endswith('/'):
            base_url += '/'

        rewritten_content = rewriter.rewrite_html(content, base_url)
        return HTMLResponse(content=rewritten_content)

    elif suffix == '.css':
        content_type = 'text/css'
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        rewritten_content = rewriter.rewrite_css(content)
        return Response(content=rewritten_content, media_type=content_type)

    elif suffix == '.js':
        content_type = 'application/javascript'
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        rewritten_content = rewriter.rewrite_js(content)
        return Response(content=rewritten_content, media_type=content_type)

    # Serve all other files directly without modification
    return FileResponse(file_path)


def main():
    parser = argparse.ArgumentParser(description='Host a website with automatic domain rewriting')
    parser.add_argument('domain', help='The original domain to rewrite (e.g., originaldomain.com)')
    parser.add_argument('-p', '--port', type=int, default=8000, help='Port to listen on (default: 8000)')
    parser.add_argument('-d', '--directory', default='.', help='Root directory of the website files (default: current directory)')
    parser.add_argument('-H', '--host', default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')

    args = parser.parse_args()

    os.environ.update({
        'DOMAIN': args.domain,
        'DIRECTORY': args.directory,
    })

    print(f"Starting server for {args.domain} at http://{args.host}:{args.port}")
    print(f"Serving content from: {os.path.abspath(args.directory)}")
    print("Press Ctrl+C to stop the server")

    uvicorn.run('main:app', host=args.host, port=args.port, reload=True)

if __name__ == "__main__":
    main()

