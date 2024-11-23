from flickr_to_google import PhotoTransferer
import logging

def main():
    try:
        transferer = PhotoTransferer()
        
        print("Retrieving Flickr albums...")
        albums = transferer.get_flickr_albums()
        
        print(f"\nNumber of albums found: {len(albums)}")
        print("\nAvailable options:")
        print("1. Transfer a specific album")
        print("2. Transfer all albums")
        print("q. Quit")
        
        while True:
            choice = input("\nSelect an option: ")
            
            if choice.lower() == 'q':
                break
                
            elif choice == '1':
                # Display album list
                print("\nAlbum list:")
                for i, album in enumerate(albums, 1):
                    print(f"{i}. {album['title']['_content']} ({album['photos']} photos)")
                
                while True:
                    album_choice = input("\nEnter album number to transfer (or 'r' to return): ")
                    if album_choice.lower() == 'r':
                        break
                    
                    try:
                        index = int(album_choice) - 1
                        if 0 <= index < len(albums):
                            album = albums[index]
                            print(f"\nTransferring album: {album['title']['_content']}")
                            result = transferer.transfer_album(album)
                            print(f"\nTransfer results:")
                            print(f"- Photos found: {result['total']}")
                            print(f"- Already existing: {result['skipped']}")
                            print(f"- Newly transferred: {result['transferred']}")
                            print(f"- Transfer failed: {result['failed']}")
                        else:
                            print("Invalid album number")
                    except ValueError:
                        print("Please enter a valid number")
                    except Exception as e:
                        print(f"Transfer error: {str(e)}")
                        
            elif choice == '2':
                print("\nStarting transfer of all albums...")
                google_albums = transferer.get_google_albums()  # Get once
                
                for album in albums:
                    try:
                        print(f"\nProcessing album: {album['title']['_content']}")
                        result = transferer.transfer_album(album, google_albums)
                        print(f"\nTransfer results:")
                        print(f"- Photos found: {result['total']}")
                        print(f"- Already existing: {result['skipped']}")
                        print(f"- Newly transferred: {result['transferred']}")
                        print(f"- Transfer failed: {result['failed']}")
                    except Exception as e:
                        print(f"Error transferring album {album['title']['_content']}: {str(e)}")
                        
                print("\nComplete transfer finished")
            
            else:
                print("Invalid option")
                
    except Exception as e:
        print(f"Error: {str(e)}")
        logging.error(f"Main error: {str(e)}")

if __name__ == "__main__":
    main() 