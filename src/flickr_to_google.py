from flickrapi import FlickrAPI
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import os
import requests
import time
from datetime import datetime
import logging
from dotenv import load_dotenv

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
            flow = InstalledAppFlow.from_client_secrets_file(
                'client_secrets.json',
                self.SCOPES
            )
            self.credentials = flow.run_local_server(port=0)
            service = build('photoslibrary', 'v1', 
                           credentials=self.credentials,
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
        """Retrieves all existing Google Photos albums"""
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
            
            return albums
        except Exception as e:
            logging.error(f"Error during Google Photos album retrieval: {str(e)}")
            raise

    def get_album_photos(self, album_id):
        """Retrieves all photos from a Google Photos album"""
        try:
            photos = []
            page_token = None
            
            while True:
                response = self.google_photos.mediaItems().search(
                    body={
                        'albumId': album_id,
                        'pageSize': 100,
                        'pageToken': page_token
                    }
                ).execute()
                
                if 'mediaItems' in response:
                    for item in response['mediaItems']:
                        # Extract only the base filename
                        filename = os.path.splitext(item['filename'])[0]
                        # Clean name (remove special characters and spaces)
                        clean_name = ''.join(e for e in filename if e.isalnum()).lower()
                        photos.append({
                            'id': item['id'],
                            'clean_name': clean_name,
                            'original_name': item['filename']
                        })
                
                page_token = response.get('nextPageToken')
                if not page_token:
                    break
            
            return photos
        except Exception as e:
            logging.error(f"Error during photo retrieval: {str(e)}")
            raise

    def transfer_album(self, flickr_album, google_albums=None):
        try:
            album_name = flickr_album['title']['_content']
            logging.info(f"Starting transfer of album: {album_name}")
            
            # Get Flickr album photo count
            flickr_photo_count = int(flickr_album['photos'])
            logging.info(f"Flickr album '{album_name}' contains {flickr_photo_count} items")
            
            # Check if album already exists
            if google_albums is None:
                google_albums = self.get_google_albums()
                
            existing_album = next(
                (album for album in google_albums 
                 if album['title'] == album_name),
                None
            )
            
            if existing_album:
                # Get Google Photos album media count
                existing_photos = self.get_album_photos(existing_album['id'])
                google_photo_count = len(existing_photos)
                logging.info(f"Google Photos album '{album_name}' contains {google_photo_count} items")
                
                # Check if counts match
                if flickr_photo_count == google_photo_count:
                    message = f"Album '{album_name}' already fully transferred (both have {flickr_photo_count} items) - skipping"
                    print(message)
                    logging.info(message)
                    return {
                        'album_name': album_name,
                        'total': flickr_photo_count,
                        'transferred': 0,
                        'skipped': flickr_photo_count,
                        'failed': 0,
                        'status': 'skipped_complete'
                    }
                
                google_album = existing_album
                logging.info(f"Found existing album: {album_name} (ID: {existing_album['id']})")
                logging.info(f"Will check for missing items ({flickr_photo_count - google_photo_count} items difference)")
            else:
                # Create a new album
                album_body = {
                    'album': {'title': album_name}
                }
                google_album = self.google_photos.albums().create(body=album_body).execute()
                logging.info(f"Created new album: {album_name} (ID: {google_album['id']})")
                existing_photos = []
            
            # Retrieve photos from the Flickr album
            self._check_flickr_quota()
            photos = self.flickr.photosets.getPhotos(
                photoset_id=flickr_album['id'],
                extras='url_o,original_format'
            )
            
            total_photos = len(photos['photoset']['photo'])
            print(f"\nTotal number of photos in the Flickr album: {total_photos}")
            
            transferred_photos = 0
            skipped_photos = 0
            failed_photos = 0
            
            # Create a set of cleaned names for quick search
            existing_photo_names = {
                photo['clean_name'] for photo in existing_photos
            }
            
            print("\nStarting photo analysis...")
            logging.info(f"Starting photo analysis for album: {album_name}")
            
            MAX_RETRIES = 3
            
            for i, photo in enumerate(photos['photoset']['photo'], 1):
                retry_count = 0
                transfer_success = False
                last_error = None
                
                while retry_count < MAX_RETRIES and not transfer_success:
                    try:
                        if retry_count > 0:
                            retry_message = f"Retry attempt {retry_count}/{MAX_RETRIES} for '{photo_title}'"
                            print(retry_message)
                            logging.info(retry_message)
                            time.sleep(2 * retry_count)  # Exponential backoff
                        
                        photo_info = self.flickr.photos.getInfo(photo_id=photo['id'])
                        photo_title = photo_info['photo']['title']['_content']
                        
                        # Check if it's a video
                        is_video = photo_info['photo'].get('media') == 'video'
                        logging.info(f"Processing {'video' if is_video else 'photo'} {i}/{total_photos}: {photo_title}")
                        
                        clean_name = ''.join(e for e in photo_title if e.isalnum()).lower()
                        
                        if clean_name in existing_photo_names:
                            print(f"Media {i}/{total_photos}: '{photo_title}' - already exists ✓")
                            logging.info(f"Media {i}/{total_photos}: '{photo_title}' - skipped (already exists)")
                            skipped_photos += 1
                            transfer_success = True
                            break
                        
                        # Get media sizes/formats
                        if is_video:
                            video_info = self.flickr.photos.getSizes(photo_id=photo['id'])
                            available_formats = video_info['sizes']['size']
                            available_formats.sort(key=lambda x: int(x.get('width', 0) or 0), reverse=True)
                            best_quality = next((fmt for fmt in available_formats if fmt.get('media') == 'video'), None)
                            
                            if not best_quality:
                                raise Exception("No video format available")
                                
                            media_url = best_quality['source']
                            logging.info(f"Downloading video: {media_url}")
                        else:
                            sizes = self.flickr.photos.getSizes(photo_id=photo['id'])
                            available_sizes = sizes['sizes']['size']
                            available_sizes.sort(key=lambda x: int(x.get('width', 0)), reverse=True)
                            best_quality = available_sizes[0]
                            media_url = best_quality['source']
                            logging.info(f"Downloading photo: {media_url}")
                        
                        # Download media
                        response = requests.get(
                            media_url,
                            timeout=120,  # Increased timeout for videos
                            headers={
                                'User-Agent': 'FlickrToGooglePhotos/1.0',
                                'Referer': 'https://www.flickr.com/'
                            },
                            stream=True  # Stream for large files
                        )
                        response.raise_for_status()
                        
                        # Upload to Google Photos
                        self._upload_to_google_photos(response.content, google_album['id'])
                        transferred_photos += 1
                        print(f"Media {i}/{total_photos}: '{photo_title}' - transferred successfully ↑")
                        logging.info(f"Successfully transferred {'video' if is_video else 'photo'} {i}/{total_photos}: {photo_title}")
                        transfer_success = True
                        
                    except Exception as e:
                        last_error = str(e)
                        retry_count += 1
                        if retry_count < MAX_RETRIES:
                            logging.warning(f"Transfer attempt {retry_count} failed for '{photo_title}': {last_error}")
                        else:
                            failed_photos += 1
                            error_message = f"Media {i}/{total_photos}: '{photo_title}' - transfer failed after {MAX_RETRIES} attempts ✗ ({last_error})"
                            print(error_message)
                            logging.error(error_message)
                
                if not transfer_success and retry_count >= MAX_RETRIES:
                    continue
            
            summary_message = (
                f"\nTransfer summary for album '{album_name}':\n"
                f"- Total media items found: {total_photos}\n"
                f"- Already existing: {skipped_photos}\n"
                f"- Successfully transferred: {transferred_photos}\n"
                f"- Failed transfers: {failed_photos}"
            )
            print(summary_message)
            logging.info(summary_message)
            
            logging.info(f"Album transfer complete: {album_name}")
            logging.info(f"Summary - Total: {total_photos}, Transferred: {transferred_photos}, "
                        f"Skipped: {skipped_photos}, Failed: {failed_photos}")
            
            return {
                'album_name': flickr_album['title']['_content'],
                'total': total_photos,
                'transferred': transferred_photos,
                'skipped': skipped_photos,
                'failed': failed_photos
            }
            
        except APIQuotaExceeded as e:
            logging.warning(str(e))
            raise
        except Exception as e:
            logging.error(f"Error during album transfer: {str(e)}")
            raise

    def _upload_to_google_photos(self, photo_bytes, album_id):
        """
        Upload a photo to Google Photos and add it to the album
        """
        try:
            logging.info(f"Starting photo upload to album ID: {album_id}")
            
            # 1. Upload the photo
            upload_url = 'https://photoslibrary.googleapis.com/v1/uploads'
            headers = {
                'Authorization': f'Bearer {self.credentials.token}',
                'Content-Type': 'application/octet-stream',
                'X-Goog-Upload-Protocol': 'raw'
            }

            upload_response = requests.post(upload_url, data=photo_bytes, headers=headers)
            upload_response.raise_for_status()
            upload_token = upload_response.content.decode('utf-8')
            logging.info("Upload token obtained successfully")

            # 2. Create media item
            request_body = {
                'newMediaItems': [{
                    'simpleMediaItem': {
                        'uploadToken': upload_token
                    }
                }]
            }

            # 3. Create item in Google Photos with album ID
            request_body['albumId'] = album_id  # Add album ID directly to creation request

            response = self.google_photos.mediaItems().batchCreate(
                body=request_body
            ).execute()

            if not response.get('newMediaItemResults'):
                raise Exception("Failed to create media item")

            status = response['newMediaItemResults'][0]['status']
            if status.get('message') == 'Success':
                logging.info(f"Photo successfully added to album ID: {album_id}")
            else:
                logging.error(f"Failed to add photo to album: {status.get('message')}")
                raise Exception(f"Upload error: {status.get('message')}")

            return True

        except Exception as e:
            logging.error(f"Error uploading to Google Photos: {str(e)}")
            raise