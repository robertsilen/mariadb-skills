package collector

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Mode string

const (
	ModeEnv     Mode = "environment"
	ModeMetrics Mode = "metrics"
)

type Context struct {
	Context          context.Context
	Mode             Mode
	OutputDir        string
	MariaDBClient    string
	MariaDBAdminPath string
	MariaDBOptions   []string
	IncludeOS        bool
	IncludeDB        bool
	RDS              bool
	Verbose          bool
}

func (c *Context) Logf(format string, args ...any) {
	if c.Verbose {
		fmt.Fprintf(os.Stderr, format+"\n", args...)
	}
}

func (c *Context) WriteFile(name string, data []byte) error {
	path := c.Path(name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

func (c *Context) AppendFile(name string, data []byte) error {
	path := c.Path(name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(data)
	return err
}

func (c *Context) Path(name string) string {
	clean := filepath.Clean(name)
	if clean == "." || strings.HasPrefix(clean, "..") || filepath.IsAbs(clean) {
		clean = filepath.Base(clean)
	}
	return filepath.Join(c.OutputDir, clean)
}

func (c *Context) CaptureFile(outputName, sourcePath string) error {
	data, err := os.ReadFile(sourcePath)
	if err != nil {
		return err
	}
	return c.WriteFile(outputName, data)
}

func (c *Context) CaptureCommand(outputName string, command string, args ...string) error {
	out, err := c.CommandOutput(command, args...)
	if err != nil {
		out = append(out, []byte("\nERROR: "+err.Error()+"\n")...)
	}
	return c.WriteFile(outputName, out)
}

func (c *Context) CommandOutput(command string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(c.Context, command, args...)
	return cmd.CombinedOutput()
}

func (c *Context) MariaDB(outputName, query string, args ...string) error {
	fullArgs := append([]string{}, c.MariaDBOptions...)
	fullArgs = append(fullArgs, args...)
	fullArgs = append(fullArgs, "-e", query)
	return c.CaptureCommand(outputName, c.MariaDBClient, fullArgs...)
}

func (c *Context) MariaDBRaw(query string, args ...string) ([]byte, error) {
	fullArgs := append([]string{}, c.MariaDBOptions...)
	fullArgs = append(fullArgs, args...)
	fullArgs = append(fullArgs, "-e", query)
	return c.CommandOutput(c.MariaDBClient, fullArgs...)
}

func (c *Context) MariaDBAdmin(outputName string, args ...string) error {
	fullArgs := append([]string{}, c.MariaDBOptions...)
	fullArgs = append(fullArgs, args...)
	return c.CaptureCommand(outputName, c.MariaDBAdminPath, fullArgs...)
}

func SplitOptions(s string) []string {
	return strings.Fields(s)
}

func FirstAvailable(names ...string) string {
	for _, name := range names {
		if path, err := exec.LookPath(name); err == nil {
			return path
		}
	}
	if len(names) == 0 {
		return ""
	}
	return names[0]
}

func Hostname() string {
	host, err := os.Hostname()
	if err != nil || host == "" {
		return "localhost"
	}
	return host
}

func Timestamp() string {
	return time.Now().Format("2006-01-02_15-04-05")
}

func DateStamp() string {
	return time.Now().Format("2006-01-02")
}

func TarGzDir(sourceDir, targetPath string) error {
	out, err := os.Create(targetPath)
	if err != nil {
		return err
	}
	defer out.Close()

	gzw := gzip.NewWriter(out)
	defer gzw.Close()

	tw := tar.NewWriter(gzw)
	defer tw.Close()

	sourceDir = filepath.Clean(sourceDir)
	return filepath.WalkDir(sourceDir, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if path == sourceDir {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return err
		}
		header, err := tar.FileInfoHeader(info, "")
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(filepath.Dir(sourceDir), path)
		if err != nil {
			return err
		}
		header.Name = filepath.ToSlash(rel)
		if err := tw.WriteHeader(header); err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		in, err := os.Open(path)
		if err != nil {
			return err
		}
		defer in.Close()
		_, err = io.Copy(tw, in)
		return err
	})
}

func IsNotFound(err error) bool {
	var pathErr *os.PathError
	return errors.As(err, &pathErr) && os.IsNotExist(pathErr.Err)
}
