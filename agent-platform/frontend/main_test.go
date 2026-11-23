package main

import (
	"bufio"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestProxyPreservesStatusAndUsesBackendToken(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer backend-secret" {
			t.Error("backend token missing")
		}
		if r.URL.Path != "/chat" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(422)
		io.WriteString(w, `{"detail":"invalid request"}`)
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	handler := newHandler(target, "backend-secret", "admin", "password")
	request := httptest.NewRequest("POST", "/api/chat", strings.NewReader(`{}`))
	request.SetBasicAuth("admin", "password")
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != 422 || !strings.Contains(recorder.Body.String(), "invalid request") {
		t.Fatalf("status/body changed: %d %s", recorder.Code, recorder.Body)
	}
}

func TestAuthenticationOriginAndEmbeddedAssets(t *testing.T) {
	target, _ := url.Parse("http://127.0.0.1:1")
	handler := newHandler(target, "", "admin", "password")
	unauthorized := httptest.NewRecorder()
	handler.ServeHTTP(unauthorized, httptest.NewRequest("GET", "/", nil))
	if unauthorized.Code != 401 {
		t.Fatal("page is not protected")
	}
	for _, item := range []struct{ path, contentType string }{
		{"/", "text/html"}, {"/static/app.js", "javascript"}, {"/static/style.css", "text/css"},
	} {
		r := httptest.NewRequest("GET", item.path, nil)
		r.SetBasicAuth("admin", "password")
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 200 || !strings.Contains(w.Header().Get("Content-Type"), item.contentType) {
			t.Errorf("asset %s failed: %d %s", item.path, w.Code, w.Header())
		}
	}
	r := httptest.NewRequest("POST", "/api/chat", strings.NewReader(`{}`))
	r.SetBasicAuth("admin", "password")
	r.Header.Set("Origin", "https://other.example")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatal("cross-origin write accepted")
	}
	removed := httptest.NewRecorder()
	r = httptest.NewRequest("GET", "/uploads/private.pdf", nil)
	r.SetBasicAuth("admin", "password")
	handler.ServeHTTP(removed, r)
	if removed.Code != 404 {
		t.Fatal("uploads still publicly served")
	}
}

func TestProxyFlushesBeforeBackendFinishes(t *testing.T) {
	release := make(chan struct{})
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: {\"type\":\"thinking\"}\n\n")
		w.(http.Flusher).Flush()
		select {
		case <-release:
		case <-r.Context().Done():
		}
	}))
	defer backend.Close()
	defer close(release)
	target, _ := url.Parse(backend.URL)
	frontend := httptest.NewServer(newHandler(target, "", "", ""))
	defer frontend.Close()
	client := &http.Client{Timeout: 2 * time.Second}
	response, err := client.Post(frontend.URL+"/api/chat/stream", "application/json", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	line, err := bufio.NewReader(response.Body).ReadString('\n')
	if err != nil || !strings.Contains(line, "thinking") {
		t.Fatalf("stream was buffered: %q %v", line, err)
	}
}

func TestUploadProxyAndBodyLimit(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/files/upload" || !strings.HasPrefix(r.Header.Get("Content-Type"), "multipart/form-data") {
			t.Error("upload was not forwarded intact")
		}
		io.WriteString(w, `{"success":true}`)
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	handler := newHandler(target, "", "", "")
	r := httptest.NewRequest("POST", "/api/chat/upload", strings.NewReader("test"))
	r.Header.Set("Content-Type", "multipart/form-data; boundary=test")
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
	r = httptest.NewRequest("POST", "/api/chat/upload", nil)
	r.ContentLength = 52 << 20
	w = httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	if w.Code != 413 {
		t.Fatal("large upload accepted")
	}
}

func TestProxyPropagatesClientCancellation(t *testing.T) {
	canceled := make(chan struct{})
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: thinking\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(canceled)
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	frontend := httptest.NewServer(newHandler(target, "", "", ""))
	defer frontend.Close()
	response, err := http.Post(frontend.URL+"/api/chat/stream", "application/json", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	bufio.NewReader(response.Body).ReadString('\n')
	response.Body.Close()
	select {
	case <-canceled:
	case <-time.After(2 * time.Second):
		t.Fatal("client cancellation did not reach backend")
	}
}

func TestKnowledgeImportAndSourceRoutes(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"path":%q}`, r.URL.Path)
	}))
	defer backend.Close()
	target, _ := url.Parse(backend.URL)
	handler := newHandler(target, "", "", "")
	for _, route := range []struct{ method, path string }{
		{"POST", "/knowledge/preview"}, {"POST", "/knowledge/import"}, {"GET", "/knowledge/sources/KB-1234567890abcdef"},
	} {
		r := httptest.NewRequest(route.method, "/api"+route.path, strings.NewReader("{}"))
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != 200 || !strings.Contains(w.Body.String(), route.path) {
			t.Fatalf("route failed: %s %d", route.path, w.Code)
		}
	}
	for _, route := range []struct {
		path     string
		expected int
	}{
		{"/api/knowledge/preview", 200}, {"/api/knowledge/import", 413},
	} {
		r := httptest.NewRequest("POST", route.path, strings.NewReader(strings.Repeat("x", 3<<20)))
		w := httptest.NewRecorder()
		handler.ServeHTTP(w, r)
		if w.Code != route.expected {
			t.Fatalf("body limit: %s %d", route.path, w.Code)
		}
	}
}
