package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/petr5092/horse_search/services/annotation-go/internal/postgres"
)

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", healthHandler)

	addr := os.Getenv("ANNOTATION_LISTEN_ADDR")
	if addr == "" {
		addr = ":8081"
	}
	databaseURL := os.Getenv("DATABASE_URL")
	if databaseURL == "" {
		log.Fatal("переменная DATABASE_URL не задана")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	pool, err := postgres.NewPool(ctx, databaseURL)
	cancel()
	if err != nil {
		log.Fatal(err)
	}
	defer pool.Close()
	log.Printf("Сервис запущен на %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func healthHandler(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"status":"ok"}`))
}
