from google import genai

TEST_API_KEY = "AIzaSyBnkkzxaj8XaXk1AYdDt-5q6lj7rLngoR8"

try:
    print("API Anahtarinin yetkili oldugu modeller listeleniyor...\n")
    client = genai.Client(api_key=TEST_API_KEY)
    
    # Senin anahtarina acik olan tum modelleri ceker
    for m in client.models.list():
        print(m.name)
        
    print("\n✅ Listeleme tamamlandi.")
except Exception as e:
    print("\n❌ HATA:")
    print(str(e))