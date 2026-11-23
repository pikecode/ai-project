package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"embed"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"time"
)

//go:embed templates/index.html static/app.js static/style.css
var assets embed.FS

func getEnv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func newHandler(target *url.URL, token, username, password string) http.Handler {
	proxy := &httputil.ReverseProxy{
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(target)
			r.Out.URL.Path = strings.TrimPrefix(r.In.URL.Path, "/api")
			if r.In.URL.Path == "/api/chat/upload" {
				r.Out.URL.Path = "/files/upload"
			}
			r.Out.Header.Del("Authorization")
			if token != "" {
				r.Out.Header.Set("Authorization", "Bearer "+token)
			}
		},
		Transport: &http.Transport{
			DialContext:           (&net.Dialer{Timeout: 10 * time.Second}).DialContext,
			ResponseHeaderTimeout: 190 * time.Second,
			IdleConnTimeout:       90 * time.Second,
		},
		FlushInterval: -1,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			log.Printf("API proxy error: %v", err)
			http.Error(w, `{"detail":"后端服务不可用或请求超时"}`, http.StatusBadGateway)
		},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) {
		method := ""
		switch r.URL.Path {
		case "/api/chat", "/api/chat/stream", "/api/chat/upload", "/api/knowledge/add", "/api/knowledge/search", "/api/knowledge/preview", "/api/knowledge/import":
			method = http.MethodPost
		case "/api/conversations", "/api/knowledge/stats", "/api/runtime":
			method = http.MethodGet
		default:
			if strings.HasPrefix(r.URL.Path, "/api/knowledge/sources/") {
				method = http.MethodGet
			} else if strings.HasPrefix(r.URL.Path, "/api/conversations/") && strings.HasSuffix(r.URL.Path, "/messages") {
				method = http.MethodGet
			}
		}
		if method == "" {
			http.NotFound(w, r)
			return
		}
		if r.Method != method {
			w.Header().Set("Allow", method)
			http.Error(w, `{"detail":"Method not allowed"}`, http.StatusMethodNotAllowed)
			return
		}
		limit := int64(2 << 20)
		if r.URL.Path == "/api/chat/upload" || r.URL.Path == "/api/knowledge/preview" {
			limit = 51 << 20
		}
		if r.ContentLength > limit {
			http.Error(w, `{"detail":"请求内容过大"}`, http.StatusRequestEntityTooLarge)
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, limit)
		proxy.ServeHTTP(w, r)
	})
	static, _ := fs.Sub(assets, "static")
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.FS(static))))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		page, _ := assets.ReadFile("templates/index.html")
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(page)
	})
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		if password != "" {
			user, pass, ok := r.BasicAuth()
			userHash, wantUser := sha256.Sum256([]byte(user)), sha256.Sum256([]byte(username))
			passHash, wantPass := sha256.Sum256([]byte(pass)), sha256.Sum256([]byte(password))
			validUser := subtle.ConstantTimeCompare(userHash[:], wantUser[:])
			validPass := subtle.ConstantTimeCompare(passHash[:], wantPass[:])
			if !ok || validUser&validPass != 1 {
				w.Header().Set("WWW-Authenticate", `Basic realm="Agent Platform", charset="UTF-8"`)
				http.Error(w, `{"detail":"Authentication required"}`, http.StatusUnauthorized)
				return
			}
		}
		// 浏览器跨站写请求不得借用已有 Basic 凭据。
		if r.Method != http.MethodGet && r.Method != http.MethodHead {
			if r.Header.Get("Sec-Fetch-Site") == "cross-site" {
				http.Error(w, `{"detail":"Cross-site request rejected"}`, http.StatusForbidden)
				return
			}
			if origin := r.Header.Get("Origin"); origin != "" {
				parsed, err := url.Parse(origin)
				if err != nil || parsed.Host != r.Host {
					http.Error(w, `{"detail":"Cross-site request rejected"}`, http.StatusForbidden)
					return
				}
			}
		}
		mux.ServeHTTP(w, r)
	})
}

func main() {
	target, err := url.Parse(getEnv("AGENT_API_URL", "http://127.0.0.1:8000"))
	if err != nil || target.Host == "" || (target.Scheme != "http" && target.Scheme != "https") {
		log.Fatal("Invalid AGENT_API_URL")
	}
	host := getEnv("HOST", "127.0.0.1")
	password := os.Getenv("WEB_PASSWORD")
	if host != "127.0.0.1" && host != "localhost" && host != "::1" && password == "" {
		log.Fatal("WEB_PASSWORD is required when HOST is not loopback")
	}
	server := &http.Server{
		Addr:              net.JoinHostPort(host, getEnv("PORT", "8653")),
		Handler:           newHandler(target, os.Getenv("API_TOKEN"), getEnv("WEB_USERNAME", "admin"), password),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       60 * time.Second,
		WriteTimeout:      240 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Printf("Server starting on %s", server.Addr)
	if err := server.ListenAndServe(); err != nil {
		log.Fatal(fmt.Errorf("frontend: %w", err))
	}
}
