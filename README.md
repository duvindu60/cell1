# CellApp - Flask Web Application

A modern Flask web application with mobile number + OTP authentication, built with a professional dark theme and responsive design.

## 🚀 Features

- **Mobile-First Design**: Responsive UI that works on all devices
- **OTP Authentication**: Secure login using mobile number and OTP
- **Modern UI**: Professional dark theme with Bootstrap 5
- **Session Management**: Secure session handling
- **Modular Architecture**: Clean code structure with blueprints
- **Production Ready**: Optimized for hosting and deployment

## 📱 Authentication Flow

1. **Enter Mobile Number**: 10-digit mobile number validation
2. **Send OTP**: System sends OTP (currently fixed as `123456` for testing)
3. **Verify OTP**: Enter the OTP to complete login
4. **Access Dashboard**: Redirected to main application

## 🛠️ Quick Start

### Prerequisites
- Python 3.8 or higher
- pip (Python package installer)

### Installation

1. **Clone and setup**
   ```bash
   git clone <your-repo-url>
   cd cellapp
   python -m venv venv
   
   # Activate virtual environment
   # Windows:
   venv\Scripts\activate
   # Linux/Mac:
   source venv/bin/activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application**
   ```bash
   python app.py
   ```

4. **Test the app**
   - Go to `http://localhost:5000/login`
   - Enter mobile number: `1234567890`
   - Enter OTP: `123456`
   - You're logged in! 🎉

## 🚀 Production Deployment

### Using Gunicorn (Recommended)
```bash
# Install gunicorn
pip install gunicorn

# Run production server
gunicorn -w 4 -b 0.0.0.0:8000 wsgi:application
```

### Environment Variables
Create a `.env` file:
```env
SECRET_KEY=your-secret-key-here
SUPABASE_URL=your-supabase-url
SUPABASE_ANON_KEY=your-supabase-anon-key
FLASK_ENV=production
```

## 📁 Project Structure

```
cellapp/
├── app.py                 # Main Flask application
├── wsgi.py               # WSGI entry point for production
├── config.py             # Configuration management
├── requirements.txt      # Python dependencies
├── DEPLOYMENT.md         # Production deployment guide
├── routes/               # Application routes (blueprints)
│   ├── auth.py          # Authentication routes
│   ├── main.py          # Main application routes
│   └── api.py           # API routes
├── templates/            # HTML templates
│   ├── base.html        # Base template
│   └── auth/            # Authentication templates
│       └── login.html   # Login page
└── static/              # Static assets
    ├── css/
    ├── js/
    └── manifest.json
```

## 🎨 UI Features

- **Responsive Design**: Mobile-first approach
- **Dark Theme**: Professional black/white color scheme
- **Modern Icons**: Font Awesome integration
- **Bootstrap 5**: Latest UI components

## 🔐 Security

- **Session Management**: Secure session handling
- **Input Validation**: Mobile number format validation
- **Secure Cookies**: HTTPOnly and SameSite settings
- **Production Ready**: Optimized security settings

## 📚 Documentation

- **Setup Guide**: This README
- **Deployment Guide**: See `DEPLOYMENT.md`
- **Environment Variables**: See `env_example.txt`

## 🆘 Support

For issues or questions:
1. Check terminal for error messages
2. Verify environment variables
3. Ensure all dependencies are installed
4. Check browser console for errors

## 📄 License

MIT License - see LICENSE file for details.