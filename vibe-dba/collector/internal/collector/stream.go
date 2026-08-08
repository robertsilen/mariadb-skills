package collector

import (
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

type StreamTask struct {
	Name     string
	Every    time.Duration
	FileName string
	Run      func(*Context) ([]byte, error)
}

func RunPeriodic(ctx *Context, task StreamTask) error {
	if task.Every <= 0 {
		task.Every = time.Second
	}
	path := ctx.Path(task.FileName)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()

	gzw := gzip.NewWriter(f)
	defer gzw.Close()

	ticker := time.NewTicker(task.Every)
	defer ticker.Stop()
	for {
		if err := writePeriodicSample(ctx, gzw, task); err != nil {
			_, _ = fmt.Fprintf(gzw, "ERROR: %v\n", err)
		}
		select {
		case <-ctx.Context.Done():
			return nil
		case <-ticker.C:
		}
	}
}

func writePeriodicSample(ctx *Context, w io.Writer, task StreamTask) error {
	_, _ = fmt.Fprintf(w, "#TS %s\n", time.Now().Format("2006-01-02_15-04-05"))
	data, err := task.Run(ctx)
	if len(data) > 0 {
		_, _ = w.Write(data)
		if data[len(data)-1] != '\n' {
			_, _ = w.Write([]byte("\n"))
		}
	}
	return err
}

func RunStreamingCommand(ctx context.Context, outputPath string, command string, args ...string) error {
	if err := os.MkdirAll(filepath.Dir(outputPath), 0o755); err != nil {
		return err
	}
	out, err := os.Create(outputPath)
	if err != nil {
		return err
	}
	defer out.Close()
	gzw := gzip.NewWriter(out)
	defer gzw.Close()

	cmd := exec.CommandContext(ctx, command, args...)
	cmd.Stdout = gzw
	cmd.Stderr = gzw
	if err := cmd.Start(); err != nil {
		return err
	}
	err = cmd.Wait()
	if ctx.Err() != nil {
		return nil
	}
	return err
}
