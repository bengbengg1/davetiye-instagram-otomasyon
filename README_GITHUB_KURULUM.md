# DAVETİYE INSTAGRAM — iPad uyumlu 17 görünür dosya

Bu ZIP iPad / iPhone için hazırlanmıştır. **17 dosyanın tamamı görünür** ve tamamı ZIP’in en üstünde yer alır. ZIP içinde `.github` gibi iOS'un gizlediği bir klasör yoktur.

## GitHub'a yükleme

1. ZIP'i Dosyalar'da aç. Çıkan 17 dosyanın **hepsini** depoya yükle, Commit changes yap. 49 başlık + 3 açıklama + 14 şarkı ve Python kaynakları yüklenmiş olur.
2. **ÖNEMLİ: 3 workflow dosyasını GitHub üzerinde taşımak gerekir.** GitHub Actions `.yml` dosyalarını ancak `.github/workflows/` klasöründeyken çalıştırır. Bu ZIP'te görünür olmaları için şu üçü depo kökündedir:
   - `davetiye-instagram-daily.yml`
   - `davetiye-instagram-library.yml`
   - `davetiye-instagram-r2-setup.yml`
3. GitHub'da her birine gir → kalem simgesi (Edit this file) → en üstteki dosya adını `.github/workflows/davetiye-instagram-daily.yml` (diğerlerinde kendi dosya adıyla) olarak değiştir → **Commit changes**. Üçünü birer birer yap. Üçü taşındıktan sonra Actions'ta otomasyonlar listelenir.
4. Settings → Secrets and variables → Actions bölümüne R2 erişim bilgilerini tanımla: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Mevcut TikTok R2 bucket adı `piyaso-tiktok-assets` kullanılır; Davetiye alt klasörleri ayrıdır.
5. Actions → `Davetiye Instagram - R2 klasorlerini hazirla` → Run workflow.
6. Actions → `Davetiye Instagram - Kutuphane kur / durum` → Run workflow → seed.
7. R2'de `piyaso-tiktok-assets/davetiye-instagram/inbox/` içerisine kendine ait videoları yükle. GitHub Actions günlük taslakları hazırlar.

## Ne hazır, ne değil?

- İçerik: 49 benzersiz başlık (eski 42. Shrek cümlesi yok), 3 birebir Instagram açıklaması (döngü), 14 farklı şarkı (döngü).
- Video üstü yazı / taslak üretimi: GitHub Actions + R2 kurulumu sonrasında aktif edilebilir.
- **Instagram'da otomatik paylaşım ve videoya otomatik telifli şarkı ekleme bu pakette YOK.** Bunları ayrıca bağlayıp lisans/kullanım ve yayın testini yapmak gerekir.
- Bu paket tüm 17 dosyayı iOS'ta göstermeyi çözer; `.github/workflows` yerleşimini kullanıcı GitHub'da tamamlar.

Yerelde test için: `python -m unittest test_davetiye_instagram test_davetiye_library -v`
