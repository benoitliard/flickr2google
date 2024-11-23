# 🚀 Flickr to Google Photos Migration Tool

Hey there! 👋 Welcome to my little project that helps you migrate your precious memories from Flickr to Google Photos. 

## Why This Exists

So here's the deal - I used to be a big Flickr fan back in the day. It was THE place for photo storage and sharing. But times change, right? Now I'm juggling multiple cloud services, and my wallet isn't too happy about it. 

With tons of storage already available on Google (thanks, Google One!), it made sense to consolidate everything there. Yeah, it's a bit sad to leave Flickr behind - it's like saying goodbye to an old friend. But hey, times are tough, and we gotta be practical! 💸

## ✨ Features

- 📸 Transfers complete albums with original quality
- 🔄 Smart duplicate detection (no more double uploads!)
- 📁 Preserves album structure
- 🎯 Choose between single album or bulk transfer
- 💪 Handles API quotas like a champ
- 🔍 Detailed transfer logs

## 🚀 Getting Started

### Prerequisites
- Python 3.x
- A Flickr account (duh!)
- A Google account with Google Photos
- Some photos to transfer 😉

### API Setup

#### Flickr API Setup
1. Go to [Flickr App Garden](https://www.flickr.com/services/apps/create/)
2. Choose "Apply for a Non-Commercial Key"
3. Fill in the application details:
   - Application Name: "Flickr to Google Photos Migration"
   - Description: Brief description of your usage
4. After submission, you'll receive:
   - API Key (Consumer Key)
   - API Secret (Consumer Secret)

#### Google Photos API Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Google Photos Library API:
   - Go to "APIs & Services" > "Library"
   - Search for "Photos Library API"
   - Click "Enable"
4. Set up OAuth consent screen:
   - Go to "APIs & Services" > "OAuth consent screen"
   - Choose "External" user type
   - Fill in the application name and user support email
   - Add your email as a test user
5. Create credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Desktop app" as application type
   - Download the client configuration file
   - Rename it to `client_secrets.json` and place it in the project root

### Installation

1. Clone this repository:   ```bash
   git clone https://github.com/your-username/flickr-to-google-photos.git
   cd flickr-to-google-photos   ```

2. Create and activate a virtual environment:   ```bash
   # On Windows
   python -m venv venv
   .\venv\Scripts\activate

   # On macOS/Linux
   python3 -m venv venv
   source venv/bin/activate   ```

3. Install dependencies:   ```bash
   pip install -r requirements.txt   ```

4. Set up your credentials:
   - Create a `.env` file in the project root
   - Add your Flickr API keys:     ```
     FLICKR_API_KEY=YOUR_API_KEY
     FLICKR_API_SECRET=YOUR_API_SECRET     ```
   - Ensure `client_secrets.json` is in the project root directory

## 🎮 Usage

1. Run the script:   ```bash
   python src/main.py   ```

2. Follow the terminal prompts:
   - Option 1: Transfer a specific album
   - Option 2: Transfer all albums
   - q: Quit

## 📊 Transfer Results

For each transferred album, you'll see:
- Total photos found
- Already existing photos (skipped)
- Newly transferred photos
- Failed transfers

## ⚠️ Important Notes

- First-time usage requires Google Photos authorization through a browser
- The Google Photos API has a quota of 10,000 requests per day
- Flickr API has rate limits of 3,600 queries per hour
- Keep your API keys and client secrets secure and never commit them to version control
- Transfer duration depends on photo count and size
- For Google Photos API, you'll remain in "Testing" status unless you verify your app, which limits to 100 users

## 🤝 Contributing

Contributions are welcome! Feel free to:
1. Fork the project
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

Distributed under the MIT License. See `LICENSE` for more information.

## 🙏 Acknowledgments

- Flickr API
- Google Photos API
- All contributors making this project better
