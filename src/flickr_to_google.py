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
        # Configuration du logging
        logging.basicConfig(
            filename=f'transfer_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        
        # Clés API Flickr
        self.FLICKR_API_KEY = os.getenv('FLICKR_API_KEY')
        self.FLICKR_API_SECRET = os.getenv('FLICKR_API_SECRET')
        
        if not self.FLICKR_API_KEY or not self.FLICKR_API_SECRET:
            raise ValueError("Les clés API Flickr ne sont pas configurées dans le fichier .env")
        
        # Limites des API
        self.GOOGLE_PHOTOS_DAILY_UPLOADS = 75000  # Limite quotidienne
        self.FLICKR_CALLS_PER_HOUR = 3600
        self.upload_count = 0
        self.last_upload_reset = datetime.now()
        self.flickr_calls = 0
        self.last_flickr_reset = datetime.now()
        
        # Configuration Google Photos
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
                print("Vous allez être redirigé vers Flickr pour autoriser l'application...")
                self.flickr.get_request_token(oauth_callback='oob')
                authorize_url = self.flickr.auth_url(perms='write')
                
                print(f'\nOuvrez cette URL dans votre navigateur pour autoriser l\'application:')
                print(authorize_url)
                
                verifier = input('\nAprès autorisation, entrez le code de vérification ici: ').strip()
                
                self.flickr.get_access_token(verifier)
            
            self.google_photos = self._authenticate_google()
        except Exception as e:
            logging.error(f"Erreur d'initialisation: {str(e)}")
            raise

    def _check_flickr_quota(self):
        # Réinitialisation du compteur toutes les heures
        if (datetime.now() - self.last_flickr_reset).total_seconds() >= 3600:
            self.flickr_calls = 0
            self.last_flickr_reset = datetime.now()
        
        if self.flickr_calls >= self.FLICKR_CALLS_PER_HOUR:
            raise APIQuotaExceeded("Limite d'appels Flickr atteinte. Attente nécessaire.")
        
        self.flickr_calls += 1

    def _check_google_quota(self):
        # Réinitialisation du compteur tous les jours
        if (datetime.now() - self.last_upload_reset).days >= 1:
            self.upload_count = 0
            self.last_upload_reset = datetime.now()
        
        if self.upload_count >= self.GOOGLE_PHOTOS_DAILY_UPLOADS:
            raise APIQuotaExceeded("Limite quotidienne Google Photos atteinte.")
        
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
            logging.error(f"Erreur d'authentification Google: {str(e)}")
            raise
    
    def get_flickr_albums(self):
        try:
            self._check_flickr_quota()
            # D'abord, obtenir votre propre ID utilisateur
            user = self.flickr.test.login()  # Cette méthode obtient les infos de l'utilisateur authentifié
            user_id = user['user']['id']
            
            self._check_flickr_quota()
            albums = self.flickr.photosets.getList(user_id=user_id)
            return albums['photosets']['photoset']
        except APIQuotaExceeded as e:
            logging.warning(str(e))
            raise
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des albums: {str(e)}")
            raise
    
    def get_google_albums(self):
        """Récupère tous les albums Google Photos existants"""
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
                        # Vérifier que le titre existe
                        if 'title' in album:
                            albums.append(album)
                
                page_token = response.get('nextPageToken')
                if not page_token:
                    break
            
            return albums
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des albums Google: {str(e)}")
            raise

    def get_album_photos(self, album_id):
        """Récupère toutes les photos d'un album Google Photos"""
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
                        # Extraire uniquement le nom de base du fichier
                        filename = os.path.splitext(item['filename'])[0]
                        # Nettoyer le nom (enlever les caractères spéciaux et espaces)
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
            logging.error(f"Erreur lors de la récupération des photos: {str(e)}")
            raise

    def transfer_album(self, flickr_album, google_albums=None):
        try:
            # Vérifier si l'album existe déjà
            if google_albums is None:
                google_albums = self.get_google_albums()
                
            existing_album = next(
                (album for album in google_albums 
                 if album['title'] == flickr_album['title']['_content']),
                None
            )
            
            if existing_album:
                google_album = existing_album
                print(f"Album existant trouvé: {flickr_album['title']['_content']}")
                # Récupérer les photos existantes
                existing_photos = self.get_album_photos(existing_album['id'])
                print(f"Nombre de photos déjà dans l'album: {len(existing_photos)}")
            else:
                # Créer un nouvel album
                album_body = {
                    'album': {'title': flickr_album['title']['_content']}
                }
                google_album = self.google_photos.albums().create(body=album_body).execute()
                print(f"Nouvel album créé: {flickr_album['title']['_content']}")
                existing_photos = []
            
            # Obtenir les photos de l'album Flickr
            self._check_flickr_quota()
            photos = self.flickr.photosets.getPhotos(
                photoset_id=flickr_album['id'],
                extras='url_o,original_format'
            )
            
            total_photos = len(photos['photoset']['photo'])
            print(f"\nNombre total de photos dans l'album Flickr: {total_photos}")
            
            transferred_photos = 0
            skipped_photos = 0
            failed_photos = 0
            
            # Créer un set des noms nettoyés pour une recherche rapide
            existing_photo_names = {
                photo['clean_name'] for photo in existing_photos
            }
            
            print("\nDébut de l'analyse des photos...")
            for i, photo in enumerate(photos['photoset']['photo'], 1):
                try:
                    # Obtenir les infos de la photo Flickr
                    photo_info = self.flickr.photos.getInfo(photo_id=photo['id'])
                    photo_title = photo_info['photo']['title']['_content']
                    
                    # Nettoyer le nom de la photo de la même manière
                    clean_name = ''.join(e for e in photo_title if e.isalnum()).lower()
                    
                    # Vérifier si la photo existe déjà
                    if clean_name in existing_photo_names:
                        print(f"Photo {i}/{total_photos} : '{photo_title}' - déjà existante ✓")
                        skipped_photos += 1
                        continue
                    
                    # Si la photo n'existe pas, procéder au transfert
                    sizes = self.flickr.photos.getSizes(photo_id=photo['id'])
                    available_sizes = sizes['sizes']['size']
                    available_sizes.sort(key=lambda x: int(x.get('width', 0)), reverse=True)
                    best_quality = available_sizes[0]
                    
                    # Télécharger la photo
                    response = requests.get(
                        best_quality['source'],
                        timeout=30,
                        headers={
                            'User-Agent': 'FlickrToGooglePhotos/1.0',
                            'Referer': 'https://www.flickr.com/'
                        }
                    )
                    response.raise_for_status()
                    
                    # Uploader vers Google Photos
                    self._upload_to_google_photos(response.content, google_album['id'])
                    transferred_photos += 1
                    print(f"Photo {i}/{total_photos} : '{photo_title}' - transférée avec succès ↑")
                    
                except Exception as e:
                    failed_photos += 1
                    print(f"Photo {i}/{total_photos} : '{photo_title}' - échec du transfert ✗ ({str(e)})")
                    continue
            
            print(f"\nRésumé pour l'album '{flickr_album['title']['_content']}':")
            print(f"- Photos trouvées dans Flickr: {total_photos}")
            print(f"- Photos déjà existantes: {skipped_photos}")
            print(f"- Nouvelles photos transférées: {transferred_photos}")
            print(f"- Échecs de transfert: {failed_photos}")
            
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
            logging.error(f"Erreur lors du transfert de l'album: {str(e)}")
            raise

    def _upload_to_google_photos(self, photo_bytes, album_id):
        """
        Télécharge une photo vers Google Photos et l'ajoute à l'album
        """
        try:
            import requests

            # 1. Upload de la photo
            upload_url = 'https://photoslibrary.googleapis.com/v1/uploads'
            headers = {
                'Authorization': f'Bearer {self.credentials.token}',
                'Content-Type': 'application/octet-stream',
                'X-Goog-Upload-Protocol': 'raw'
            }

            # Faire l'upload
            upload_response = requests.post(upload_url, data=photo_bytes, headers=headers)
            upload_response.raise_for_status()
            upload_token = upload_response.content.decode('utf-8')

            if not upload_token:
                raise Exception("Échec de l'obtention du token d'upload")

            # 2. Créer l'élément média
            request_body = {
                'newMediaItems': [{
                    'simpleMediaItem': {
                        'uploadToken': upload_token
                    }
                }]
            }

            # 3. Créer l'élément dans Google Photos avec l'album ID
            request_body['albumId'] = album_id  # Ajouter l'album ID directement dans la requête de création

            response = self.google_photos.mediaItems().batchCreate(
                body=request_body
            ).execute()

            if not response.get('newMediaItemResults'):
                raise Exception("Échec de la création de l'élément média")

            status = response['newMediaItemResults'][0]['status']
            if status.get('message') != 'Success':
                raise Exception(f"Erreur lors de l'upload: {status.get('message')}")

            return True

        except Exception as e:
            logging.error(f"Erreur lors de l'upload vers Google Photos: {str(e)}")
            raise