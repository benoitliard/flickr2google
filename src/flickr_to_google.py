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
            # Check if album already exists
            if google_albums is None:
                google_albums = self.get_google_albums()
                
            existing_album = next(
                (album for album in google_albums 
                 if album['title'] == flickr_album['title']['_content']),
                None
            )
            
            if existing_album:
                google_album = existing_album
                print(f"Existing album found: {flickr_album['title']['_content']}")
                # Retrieve existing photos
                existing_photos = self.get_album_photos(existing_album['id'])
                print(f"Number of photos already in the album: {len(existing_photos)}")
            else:
                # Create a new album
                album_body = {
                    'album': {'title': flickr_album['title']['_content']}
                }
                google_album = self.google_photos.albums().create(body=album_body).execute()
                print(f"New album created: {flickr_album['title']['_content']}")
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
            for i, photo in enumerate(photos['photoset']['photo'], 1):
                try:
                    # Retrieve photo info from Flickr
                    photo_info = self.flickr.photos.getInfo(photo_id=photo['id'])
                    photo_title = photo_info['photo']['title']['_content']
                    
                    # Clean name of the photo in the same way
                    clean_name = ''.join(e for e in photo_title if e.isalnum()).lower()
                    
                    # Check if photo already exists
                    if clean_name in existing_photo_names:
                        print(f"Photo {i}/{total_photos} : '{photo_title}' - already exists ✓")
                        skipped_photos += 1
                        continue
                    
                    # If photo doesn't exist, proceed with transfer
                    sizes = self.flickr.photos.getSizes(photo_id=photo['id'])
                    available_sizes = sizes['sizes']['size']
                    available_sizes.sort(key=lambda x: int(x.get('width', 0)), reverse=True)
                    best_quality = available_sizes[0]
                    
                    # Download the photo
                    response = requests.get(
                        best_quality['source'],
                        timeout=30,
                        headers={
                            'User-Agent': 'FlickrToGooglePhotos/1.0',
                            'Referer': 'https://www.flickr.com/'
                        }
                    )
                    response.raise_for_status()
                    
                    # Upload to Google Photos
                    self._upload_to_google_photos(response.content, google_album['id'])
                    transferred_photos += 1
                    print(f"Photo {i}/{total_photos} : '{photo_title}' - transferred successfully ↑")
                    
                except Exception as e:
                    failed_photos += 1
                    print(f"Photo {i}/{total_photos} : '{photo_title}' - transfer failed ✗ ({str(e)})")
                    continue
            
            print(f"\nSummary for album '{flickr_album['title']['_content']}':")
            print(f"- Photos found in Flickr: {total_photos}")
            print(f"- Existing photos: {skipped_photos}")
            print(f"- New photos transferred: {transferred_photos}")
            print(f"- Transfer failures: {failed_photos}")
            
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
            import requests

            # 1. Upload the photo
            upload_url = 'https://photoslibrary.googleapis.com/v1/uploads'
            headers = {
                'Authorization': f'Bearer {self.credentials.token}',
                'Content-Type': 'application/octet-stream',
                'X-Goog-Upload-Protocol': 'raw'
            }

            # Perform the upload
            upload_response = requests.post(upload_url, data=photo_bytes, headers=headers)
            upload_response.raise_for_status()
            upload_token = upload_response.content.decode('utf-8')

            if not upload_token:
                raise Exception("Failed to obtain upload token")

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
            if status.get('message') != 'Success':
                raise Exception(f"Upload error: {status.get('message')}")

            return True

        except Exception as e:
            logging.error(f"Error during upload to Google Photos: {str(e)}")
            raise