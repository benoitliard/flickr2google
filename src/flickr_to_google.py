from flickrapi import FlickrAPI
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from oauth2client.file import Storage
import os
import requests
import time
from datetime import datetime
import logging
from dotenv import load_dotenv
import concurrent.futures  # Add for parallel processing
import numpy as np  # Add for faster array operations
import urllib3
import warnings
import threading
from google.auth.transport.requests import Request
import httplib2  # Add this import instead of build_http
from oauth2client.client import OAuth2Credentials
import google_auth_httplib2
import json

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

# Add this at the start of your script
print("FLICKR_API_KEY:", os.getenv('FLICKR_API_KEY'))
print("FLICKR_API_SECRET:", os.getenv('FLICKR_API_SECRET'))

class APIQuotaExceeded(Exception):
    pass

class PhotoTransferer:
    def __init__(self):
        # Logging configuration
        logging.basicConfig(
            filename=f'transfer_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        
        # Flickr API keys
        self.FLICKR_API_KEY = os.getenv('FLICKR_API_KEY')
        self.FLICKR_API_SECRET = os.getenv('FLICKR_API_SECRET')
        
        if not self.FLICKR_API_KEY or not self.FLICKR_API_SECRET:
            raise ValueError("Flickr API keys are not configured in the .env file")
        
        # API limits
        self.GOOGLE_PHOTOS_DAILY_UPLOADS = 75000  # Daily limit
        self.FLICKR_CALLS_PER_HOUR = 3600
        self.upload_count = 0
        self.last_upload_reset = datetime.now()
        self.flickr_calls = 0
        self.last_flickr_reset = datetime.now()
        
        # Google Photos configuration
        self.SCOPES = ['https://www.googleapis.com/auth/photoslibrary',
                      'https://www.googleapis.com/auth/photoslibrary.sharing']
        
        try:
            self.flickr = FlickrAPI(
                self.FLICKR_API_KEY, 
                self.FLICKR_API_SECRET, 
                format='parsed-json',
                store_token=True
            )
            
            if not self.flickr.token_valid(perms='write'):
                print("You will be redirected to Flickr to authorize the application...")
                self.flickr.get_request_token(oauth_callback='oob')
                authorize_url = self.flickr.auth_url(perms='write')
                
                print(f'\nOpen this URL in your browser to authorize the application:')
                print(authorize_url)
                
                verifier = input('\nAfter authorization, enter the verification code here: ').strip()
                
                self.flickr.get_access_token(verifier)
            
            self.google_photos = self._authenticate_google()
        except Exception as e:
            logging.error(f"Initialization error: {str(e)}")
            raise
        
        # Configure session with adjusted pool settings
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=100,  # Increased from 50
            pool_maxsize=100,     # Increased from 50
            max_retries=3,
            pool_block=False
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        # Adjust concurrency settings for better performance
        self.BATCH_SIZE = 2      # Réduit de 5 à 2
        self.MAX_WORKERS = 2     # Réduit de 5 à 2
        self.UPLOAD_TIMEOUT = 180
        self.DOWNLOAD_TIMEOUT = 180
        
        # Increase semaphore limit for more concurrent uploads
        self.upload_semaphore = threading.Semaphore(2)  # Réduit de 5 à 2
        
        # Add event for graceful shutdown
        self.shutdown_event = threading.Event()
        
        # Cache for album data
        self._album_cache = {}
        self._photo_cache = {}
        
        # Ajouter un délai entre les requêtes
        self.WRITE_REQUESTS_PER_MINUTE = 30  # Limite Google
        self.write_request_delay = 60 / self.WRITE_REQUESTS_PER_MINUTE  # ~2 secondes entre chaque requête

    def _check_flickr_quota(self):
        # Reset counter every hour
        if (datetime.now() - self.last_flickr_reset).total_seconds() >= 3600:
            self.flickr_calls = 0
            self.last_flickr_reset = datetime.now()
        
        if self.flickr_calls >= self.FLICKR_CALLS_PER_HOUR:
            raise APIQuotaExceeded("Flickr API call limit reached. Waiting necessary.")
        
        self.flickr_calls += 1

    def _check_google_quota(self):
        # Reset counter every day
        if (datetime.now() - self.last_upload_reset).days >= 1:
            self.upload_count = 0
            self.last_upload_reset = datetime.now()
        
        if self.upload_count >= self.GOOGLE_PHOTOS_DAILY_UPLOADS:
            raise APIQuotaExceeded("Daily Google Photos upload limit reached.")
        
        self.upload_count += 1

    def _authenticate_google(self):
        try:
            creds = None
            # The file token.json stores the user's access and refresh tokens
            if os.path.exists('token.json'):
                creds = Credentials.from_authorized_user_file('token.json', self.SCOPES)

            # If there are no (valid) credentials available, let the user log in.
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        'client_secrets.json',
                        scopes=self.SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                    
                    # Save the credentials
                    with open('token.json', 'w') as token:
                        token.write(creds.to_json())
                    logging.info("New credentials stored successfully")

            self.credentials = creds
            
            # Create authorized HTTP object
            authorized_http = google_auth_httplib2.AuthorizedHttp(
                creds, http=httplib2.Http())

            service = build('photoslibrary', 'v1', 
                          http=authorized_http,
                          static_discovery=False)
            return service
            
        except Exception as e:
            logging.error(f"Google authentication error: {str(e)}")
            raise
    
    def get_flickr_albums(self):
        try:
            self._check_flickr_quota()
            # First, get your own user ID
            user = self.flickr.test.login()  # This method gets authenticated user info
            user_id = user['user']['id']
            
            self._check_flickr_quota()
            albums = self.flickr.photosets.getList(user_id=user_id)
            return albums['photosets']['photoset']
        except APIQuotaExceeded as e:
            logging.warning(str(e))
            raise
        except Exception as e:
            logging.error(f"Error during album retrieval: {str(e)}")
            raise
    
    def get_google_albums(self):
        """Retrieves all existing Google Photos albums with caching"""
        cache_key = 'google_albums'
        if cache_key in self._album_cache:
            return self._album_cache[cache_key]
            
        try:
            albums = []
            page_token = None
            
            while True:
                response = self.google_photos.albums().list(
                    pageSize=50,
                    pageToken=page_token
                ).execute()
                
                if 'albums' in response:
                    for album in response['albums']:
                        # Check if title exists
                        if 'title' in album:
                            albums.append(album)
                
                page_token = response.get('nextPageToken')
                if not page_token:
                    break
            
            self._album_cache[cache_key] = albums
            return albums
        except Exception as e:
            logging.error(f"Error during Google Photos album retrieval: {str(e)}")
            raise

    def _normalize_filename(self, filename):
        """Normalize filename for consistent comparison"""
        # Remove file extension
        name = os.path.splitext(filename)[0]
        # Convert to lowercase
        name = name.lower()
        # Replace special characters with underscore
        name = ''.join(c if c.isalnum() else '_' for c in name)
        # Remove multiple consecutive underscores
        name = '_'.join(filter(None, name.split('_')))
        return name

    def get_album_photos(self, album_id):
        """Retrieves all photos from a Google Photos album with proper pagination"""
        try:
            photos = []
            page_token = None
            page_size = 100
            
            while True:
                logging.info(f"Fetching page of photos for album {album_id} (current count: {len(photos)})")
                
                response = self.google_photos.mediaItems().search(
                    body={
                        'albumId': album_id,
                        'pageSize': page_size,
                        'pageToken': page_token
                    }
                ).execute()
                
                if 'mediaItems' in response:
                    for item in response['mediaItems']:
                        # Stocker plus d'informations pour une meilleure comparaison
                        photos.append({
                            'id': item['id'],
                            'clean_name': self._normalize_filename(item['filename']),
                            'original_name': item['filename'],
                            'google_id': item['id'],
                            'creation_time': item.get('mediaMetadata', {}).get('creationTime'),
                            'width': item.get('mediaMetadata', {}).get('width'),
                            'height': item.get('mediaMetadata', {}).get('height'),
                            'mime_type': item.get('mimeType')
                        })
                    
                    logging.info(f"Retrieved {len(response['mediaItems'])} items in this page")
                    # Log détaillé des fichiers existants
                    for item in photos[-len(response['mediaItems']):]:
                        logging.info(f"Found existing file: {item['original_name']} (ID: {item['google_id']})")
                
                page_token = response.get('nextPageToken')
                if not page_token:
                    break
            
            return photos
            
        except Exception as e:
            logging.error(f"Error during photo retrieval: {str(e)}")
            raise

    def _get_all_flickr_photos(self, photoset_id):
        """Récupère toutes les photos d'un album Flickr avec pagination"""
        all_photos = []
        page = 1
        per_page = 500
        
        while True:
            photos = self.flickr.photosets.getPhotos(
                photoset_id=photoset_id,
                extras='url_o,original_format',
                page=page,
                per_page=per_page
            )
            
            if 'photoset' not in photos or 'photo' not in photos['photoset']:
                break
                
            all_photos.extend(photos['photoset']['photo'])
            
            if len(photos['photoset']['photo']) < per_page:
                break
                
            page += 1
            
        return all_photos

    def _transfer_single_album(self, flickr_album, google_albums=None):
        """Handle transfer of albums under the Google Photos limit"""
        executor = None
        try:
            album_name = flickr_album['title']['_content']
            
            # Check if album already exists
            if google_albums is None:
                google_albums = self.get_google_albums()
                
            existing_album = next(
                (album for album in google_albums 
                 if album['title'] == album_name),
                None
            )
            
            if existing_album:
                google_album = existing_album
                existing_photos = self.get_album_photos(existing_album['id'])
                google_photo_count = len(existing_photos)
                logging.info(f"Google Photos album '{album_name}' contains {google_photo_count} items")
                print(f"Google Photos album contains {google_photo_count} items")
                print(f"Difference: {int(flickr_album['photos']) - google_photo_count} items to transfer")
            else:
                album_body = {
                    'album': {'title': album_name}
                }
                google_album = self.google_photos.albums().create(body=album_body).execute()
                logging.info(f"Created new album: {album_name}")
                existing_photos = []
                google_photo_count = 0
            
            # Get Flickr photos with pagination
            photos = self._get_all_flickr_photos(flickr_album['id'])
            total_photos = len(photos)
            print(f"\nTotal number of photos in the Flickr album: {total_photos}")
            
            transferred_photos = 0
            skipped_photos = 0
            failed_photos = 0
            processed_photos = 0  # Nouveau compteur pour le total traité
            
            # Create set of existing photo names for quick lookup
            existing_photo_names = {photo['clean_name'] for photo in existing_photos}
            
            print("\nStarting photo analysis...")
            logging.info(f"Starting photo analysis for album: {album_name}")
            
            # Process in batches
            batch_size = self.BATCH_SIZE
            photo_batches = [
                photos[i:i + batch_size]
                for i in range(0, total_photos, batch_size)
            ]
            
            # Process batches in parallel with proper cleanup
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=self.MAX_WORKERS)
            futures = []
            
            try:
                # Submit all batches to the executor
                for batch in photo_batches:
                    if self.shutdown_event.is_set():
                        logging.info("Graceful shutdown requested")
                        break
                    future = executor.submit(self._process_photo_batch, batch, google_album['id'], existing_photos)
                    futures.append(future)
                
                # Process results with longer timeout and better error handling
                completed_futures = []
                for future in concurrent.futures.as_completed(futures):
                    try:
                        batch_results = future.result(timeout=300)
                        completed_futures.append(future)
                        
                        for result in batch_results:
                            processed_photos += 1  # Incrémenter pour chaque photo traitée
                            if result['status'] == 'transferred':
                                transferred_photos += 1
                            elif result['status'] == 'skipped':
                                skipped_photos += 1
                            else:
                                failed_photos += 1
                            
                            # Afficher la progression incluant les skipped
                            print(f"Progress: {processed_photos}/{total_photos} processed ({transferred_photos} transferred, {skipped_photos} skipped, {failed_photos} failed)")
                            
                    except concurrent.futures.TimeoutError:
                        logging.error(f"Batch processing timeout after 5 minutes")
                        batch_index = futures.index(future)
                        failed_photos += len(photo_batches[batch_index])
                        print(f"Batch {batch_index + 1} timed out - moving to next batch")
                    except Exception as e:
                        logging.error(f"Batch processing error: {str(e)}")
                        batch_index = futures.index(future)
                        failed_photos += len(photo_batches[batch_index])
                
                # Check for unfinished futures
                unfinished_futures = set(futures) - set(completed_futures)
                if unfinished_futures:
                    unfinished_count = len(unfinished_futures)
                    total_futures = len(futures)
                    logging.warning(f"{unfinished_count} (of {total_futures}) futures unfinished")
                    print(f"\nWarning: {unfinished_count} batches did not complete successfully")
                    
                    # Cancel unfinished futures
                    for future in unfinished_futures:
                        future.cancel()
                        batch_index = futures.index(future)
                        failed_photos += len(photo_batches[batch_index])
                
            except KeyboardInterrupt:
                logging.info("Received interrupt signal, initiating graceful shutdown")
                self.shutdown_event.set()
                # Cancel pending futures
                for future in futures:
                    future.cancel()
                print("\nGracefully shutting down... (this may take a moment)")
                return {
                    'album_name': album_name,
                    'total': total_photos,
                    'transferred': transferred_photos,
                    'skipped': skipped_photos,
                    'failed': failed_photos,
                    'status': 'interrupted'
                }
                
            finally:
                if executor:
                    print("Shutting down executor...")
                    executor.shutdown(wait=True, cancel_futures=True)
                    print("Executor shutdown complete")
            
            summary_message = (
                f"\nTransfer summary for album '{album_name}':\n"
                f"- Total media items found: {total_photos}\n"
                f"- Already existing: {skipped_photos}\n"
                f"- Successfully transferred: {transferred_photos}\n"
                f"- Failed transfers: {failed_photos}"
            )
            print(summary_message)
            logging.info(summary_message)
            
            return {
                'album_name': album_name,
                'total': total_photos,
                'transferred': transferred_photos,
                'skipped': skipped_photos,
                'failed': failed_photos
            }
            
        except Exception as e:
            logging.error(f"Error transferring album {album_name}: {str(e)}")
            raise
        finally:
            if executor:
                try:
                    executor.shutdown(wait=False)
                except Exception as e:
                    logging.error(f"Error shutting down executor: {str(e)}")

    def _process_photo_batch(self, photos, album_id, existing_photos):
        """Process a batch of photos with improved memory management"""
        results = []
        thread_id = threading.get_ident()
        
        logging.info(f"Starting batch processing in thread {thread_id}")
        
        for photo in photos:
            session = None
            content = None
            photo_info = None
            
            try:
                with self.upload_semaphore:
                    # Récupérer les infos de la photo dans un bloc try séparé
                    try:
                        photo_info = self.flickr.photos.getInfo(photo_id=photo['id'])
                        photo_title = photo_info['photo']['title']['_content']
                        logging.info(f"Processing file:")
                        logging.info(f"  - Original Flickr title: {photo_title}")
                        logging.info(f"  - Flickr photo ID: {photo['id']}")
                        
                        # Amélioration de la vérification des doublons
                        clean_name = self._normalize_filename(photo_title)
                        
                        # Log détaillé pour le débogage
                        logging.info(f"Checking if photo exists: {photo_title}")
                        logging.info(f"Normalized name: {clean_name}")
                        
                        # Vérification plus stricte
                        photo_exists = False
                        for existing_photo in existing_photos:
                            if (existing_photo['clean_name'] == clean_name or
                                existing_photo['original_name'] == photo_title):
                                photo_exists = True
                                logging.info(f"Found match:")
                                logging.info(f"  - Existing: {existing_photo['original_name']}")
                                logging.info(f"  - New: {photo_title}")
                                break
                        
                        if photo_exists:
                            logging.info(f"Skipping duplicate: {photo_title}")
                            results.append({
                                'photo_id': photo['id'],
                                'status': 'skipped',
                                'error': None
                            })
                            continue
                            
                        # Récupérer les tailles disponibles
                        sizes = self.flickr.photos.getSizes(photo_id=photo['id'])
                        if 'sizes' in sizes and 'size' in sizes['sizes']:
                            available_sizes = sizes['sizes']['size']
                            logging.info(f"  - Available sizes: {[size['label'] for size in available_sizes]}")
                            
                            # Trier par taille décroissante
                            available_sizes.sort(key=lambda x: int(x.get('width', 0) or 0), reverse=True)
                            best_quality = available_sizes[0]
                            media_url = best_quality['source']
                            logging.info(f"  - Selected photo size: {best_quality['label']}")
                            logging.info(f"  - Media URL: {media_url}")
                            
                            # Créer une nouvelle session pour chaque téléchargement
                            session = requests.Session()
                            adapter = requests.adapters.HTTPAdapter(max_retries=3)
                            session.mount('http://', adapter)
                            session.mount('https://', adapter)
                            
                            # Télécharger avec gestion de la mémoire
                            with session.get(media_url, stream=True, timeout=300) as response:
                                response.raise_for_status()
                                content = response.content
                                
                                # Upload immédiat après téléchargement
                                if content:
                                    upload_result = self._upload_to_google_photos(content, album_id, photo_info=photo_info)
                                    if upload_result:
                                        print(f"Media: '{photo_title}' - transferred successfully ↑")
                                        logging.info(f"  - Successfully uploaded to Google Photos")
                                        results.append({
                                            'photo_id': photo['id'],
                                            'status': 'transferred',
                                            'error': None
                                        })
                                    else:
                                        raise Exception("Upload failed")
                                else:
                                    raise Exception("Downloaded content is empty")
                                
                    except Exception as e:
                        error_msg = f"Media: '{photo_title if 'photo_title' in locals() else 'Unknown'}' - transfer failed ✗ ({str(e)})"
                        print(error_msg)
                        logging.error(error_msg)
                        results.append({
                            'photo_id': photo['id'],
                            'status': 'failed',
                            'error': str(e)
                        })
                        
            finally:
                # Nettoyage explicite des ressources
                if session:
                    session.close()
                if content:
                    del content
                if photo_info:
                    del photo_info
                session = None
                content = None
                photo_info = None
        
        return results

    def _upload_to_google_photos(self, photo_bytes, album_id, photo_info=None):
        """Upload with improved error handling and memory management"""
        MAX_RETRIES = 3
        retry_count = 0
        thread_id = threading.get_ident()
        local_session = None

        try:
            # Get photo details for better logging
            photo_title = photo_info['photo']['title']['_content'] if photo_info else 'Unknown'
            photo_id = photo_info['photo']['id'] if photo_info else 'Unknown'
            
            # Determine file type and validate
            content_type = None
            extension = photo_title.lower().split('.')[-1] if '.' in photo_title else 'jpg'
            
            # Map extensions to MIME types
            mime_types = {
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'png': 'image/png',
                'gif': 'image/gif',
                'bmp': 'image/bmp',
                'webp': 'image/webp',
                'heic': 'image/heic',
                'tiff': 'image/tiff',
                'mp4': 'video/mp4',
                'mov': 'video/quicktime',
                'avi': 'video/x-msvideo'
            }
            
            content_type = mime_types.get(extension, 'image/jpeg')
            
            # Ensure filename ends with correct extension
            if not photo_title.lower().endswith(f'.{extension}'):
                photo_title = f"{photo_title}.{extension}"
            
            logging.info(f"Thread {thread_id} - Starting upload:")
            logging.info(f"  - Photo ID: {photo_id}")
            logging.info(f"  - Title: {photo_title}")
            logging.info(f"  - Content Type: {content_type}")
            logging.info(f"  - Size: {len(photo_bytes) / 1024 / 1024:.2f} MB")

            while retry_count < MAX_RETRIES:
                try:
                    # Refresh token if needed
                    if not self.credentials.valid:
                        self.credentials.refresh(Request())

                    # Create new session for each attempt
                    local_session = requests.Session()
                    adapter = requests.adapters.HTTPAdapter(
                        max_retries=3,
                        pool_connections=1,
                        pool_maxsize=1
                    )
                    local_session.mount('https://', adapter)
                    
                    # First stage: Upload bytes
                    headers = {
                        'Authorization': f'Bearer {self.credentials.token}',
                        'Content-Type': 'application/octet-stream',
                        'X-Goog-Upload-Protocol': 'raw',
                        'X-Goog-Upload-Content-Type': content_type,
                        'X-Goog-Upload-File-Name': photo_title,
                        'User-Agent': 'flickr-to-google-photos/1.0',
                        'Accept': '*/*'
                    }

                    logging.info(f"Thread {thread_id} - Starting upload request...")
                    logging.info(f"Thread {thread_id} - Request details:")
                    logging.info(f"  - URL: https://photoslibrary.googleapis.com/v1/uploads")
                    logging.info(f"  - Headers:")
                    for key, value in headers.items():
                        # Ne pas logger le token complet pour des raisons de sécurité
                        if key == 'Authorization':
                            value = value[:30] + '...'
                        logging.info(f"    {key}: {value}")
                    logging.info(f"  - Data size: {len(photo_bytes)} bytes")
                    
                    # Use a copy of photo_bytes to prevent memory issues
                    upload_data = photo_bytes[:]
                    response = local_session.post(
                        'https://photoslibrary.googleapis.com/v1/uploads',
                        data=upload_data,
                        headers=headers,
                        timeout=60,
                        verify=True
                    )
                    
                    logging.info(f"Thread {thread_id} - Response details:")
                    logging.info(f"  - Status code: {response.status_code}")
                    logging.info(f"  - Response headers:")
                    for key, value in response.headers.items():
                        logging.info(f"    {key}: {value}")
                    
                    response.raise_for_status()
                    upload_token = response.content.decode('utf-8')
                    
                    if not upload_token:
                        raise Exception("Empty upload token received")
                    
                    logging.info(f"Thread {thread_id} - Upload token obtained: {upload_token[:10]}...")

                    # Clear response data
                    response = None
                    upload_data = None

                    # Second stage: Create media item with rate limiting
                    time.sleep(self.write_request_delay)  # Attendre ~2 secondes entre chaque requête

                    request_body = {
                        'newMediaItems': [{
                            'simpleMediaItem': {
                                'uploadToken': upload_token
                            }
                        }],
                        'albumId': album_id
                    }

                    batch_create_url = 'https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate'
                    batch_headers = {
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {self.credentials.token}'
                    }

                    batch_response = local_session.post(
                        batch_create_url,
                        json=request_body,
                        headers=batch_headers,
                        timeout=60
                    )

                    if batch_response.status_code == 429:  # Too Many Requests
                        retry_after = int(batch_response.headers.get('Retry-After', 60))
                        logging.info(f"Rate limit hit, waiting {retry_after} seconds")
                        time.sleep(retry_after)
                        continue

                    batch_response.raise_for_status()
                    result = batch_response.json()

                    if not result.get('newMediaItemResults'):
                        raise Exception("No results returned")

                    result = result['newMediaItemResults'][0]
                    status = result.get('status', {})

                    if status.get('message') == 'Success':
                        if 'mediaItem' in result:
                            media_item = result['mediaItem']
                            logging.info(f"Thread {thread_id} - Upload successful:")
                            logging.info(f"  - Media ID: {media_item.get('id')}")
                            logging.info(f"  - Google filename: {media_item.get('filename')}")
                            logging.info(f"  - Original filename: {photo_title}")
                            logging.info(f"  - Successfully uploaded to Google Photos")
                        return True
                    else:
                        raise Exception(f"Upload error: {status.get('message')}")

                except Exception as e:
                    retry_count += 1
                    logging.error(f"Thread {thread_id} - Attempt {retry_count} failed:")
                    logging.error(f"  - Photo: {photo_title}")
                    logging.error(f"  - Error: {str(e)}")
                    if hasattr(e, 'response'):
                        logging.error(f"  - Response status code: {e.response.status_code}")
                        logging.error(f"  - Response content: {e.response.content}")
                    
                    if retry_count < MAX_RETRIES:
                        wait_time = (2 ** retry_count)
                        logging.info(f"Thread {thread_id} - Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                    else:
                        raise Exception(f"Failed after {MAX_RETRIES} attempts: {str(e)}")

                finally:
                    # Clean up session
                    if local_session:
                        local_session.close()
                        local_session = None

        finally:
            # Final cleanup
            if local_session:
                local_session.close()

        return False