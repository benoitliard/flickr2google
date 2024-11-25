from flickr_to_google import PhotoTransferer
import logging

def main():
    try:
        transferer = PhotoTransferer()
        
        while True:
            print("\nOptions:")
            print("1. Transfer a specific album")
            print("2. Transfer all albums")
            print("q. Quit")
            
            choice = input("\nSelect an option: ").strip().lower()
            
            if choice == 'q':
                break
                
            if choice == '1':
                # Get list of albums
                albums = transferer.get_flickr_albums()
                
                print("\nAvailable albums:")
                for i, album in enumerate(albums, 1):
                    print(f"{i}. {album['title']['_content']} ({album['photos']} photos)")
                
                album_choice = int(input("\nSelect album number: ")) - 1
                if 0 <= album_choice < len(albums):
                    selected_album = albums[album_choice]
                    print(f"\nProcessing album: {selected_album['title']['_content']}")
                    try:
                        result = transferer._transfer_single_album(selected_album)
                        print(f"Transfer completed: {result}")
                    except Exception as e:
                        print(f"Error transferring album {selected_album['title']['_content']}: {str(e)}")
                else:
                    print("Invalid album selection")
                    
            elif choice == '2':
                print("\nStarting transfer of all albums...")
                albums = transferer.get_flickr_albums()
                
                for album in albums:
                    print(f"\nProcessing album: {album['title']['_content']}")
                    try:
                        result = transferer._transfer_single_album(album)
                        print(f"Transfer completed: {result}")
                    except Exception as e:
                        print(f"Error transferring album {album['title']['_content']}: {str(e)}")
            
            else:
                print("Invalid option")
                
    except Exception as e:
        print(f"Main error: {str(e)}")
        logging.error(f"Main error: {str(e)}")

if __name__ == "__main__":
    main() 