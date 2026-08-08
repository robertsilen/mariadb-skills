package collector

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"strconv"
	"strings"
)

type MariaDBConnection struct {
	URI          string
	DefaultsFile string
	Host         string
	Port         int
	Socket       string
	User         string
	Password     string
	Database     string
	SSLMode      string
	ExtraOptions string
}

func (c *MariaDBConnection) ApplyURI() error {
	if c.URI == "" {
		return nil
	}
	parsed, err := url.Parse(c.URI)
	if err != nil {
		return err
	}
	switch parsed.Scheme {
	case "mariadb", "mysql":
		if parsed.User != nil {
			if c.User == "" {
				c.User = parsed.User.Username()
			}
			if password, ok := parsed.User.Password(); ok && c.Password == "" {
				c.Password = password
			}
		}
		if c.Host == "" {
			c.Host = parsed.Hostname()
		}
		if parsed.Port() != "" && c.Port == 0 {
			port, err := strconv.Atoi(parsed.Port())
			if err != nil {
				return fmt.Errorf("invalid MariaDB URI port %q: %w", parsed.Port(), err)
			}
			c.Port = port
		}
		if parsed.Path != "" && parsed.Path != "/" && c.Database == "" {
			c.Database = strings.TrimPrefix(parsed.Path, "/")
		}
	case "socket":
		if c.Socket == "" {
			c.Socket = parsed.Path
			if c.Socket == "" {
				c.Socket = strings.TrimPrefix(c.URI, "socket:")
			}
		}
	default:
		return fmt.Errorf("unsupported MariaDB connection URI scheme %q", parsed.Scheme)
	}
	return nil
}

func (c MariaDBConnection) HasPassword() bool {
	return c.Password != ""
}

func (c MariaDBConnection) Options() []string {
	var args []string
	if c.DefaultsFile != "" {
		args = append(args, "--defaults-file="+c.DefaultsFile)
	}
	if c.Host != "" {
		args = append(args, "--host="+c.Host)
	}
	if c.Port > 0 {
		args = append(args, "--port="+strconv.Itoa(c.Port))
	}
	if c.Socket != "" {
		args = append(args, "--socket="+c.Socket)
	}
	if c.User != "" {
		args = append(args, "--user="+c.User)
	}
	if c.Password != "" {
		args = append(args, "--password="+c.Password)
	}
	if c.Database != "" {
		args = append(args, "--database="+c.Database)
	}
	if c.SSLMode != "" {
		args = append(args, "--ssl="+c.SSLMode)
	}
	args = append(args, SplitOptions(c.ExtraOptions)...)
	return args
}

func EnsureMariaDBConnection(ctx context.Context, client string, conn *MariaDBConnection, stdin io.Reader, stderr io.Writer) error {
	if err := conn.ApplyURI(); err != nil {
		return err
	}
	out, err := runMariaDBPing(ctx, client, conn.Options())
	if err == nil {
		return nil
	}
	if conn.HasPassword() || !looksLikeMissingPassword(out) {
		return fmt.Errorf("MariaDB connection check failed: %s", strings.TrimSpace(string(out)))
	}

	password, promptErr := PromptPassword(stdin, stderr, fmt.Sprintf("MariaDB password for %s: ", conn.User))
	if promptErr != nil {
		return promptErr
	}
	conn.Password = password
	out, err = runMariaDBPing(ctx, client, conn.Options())
	if err != nil {
		return fmt.Errorf("MariaDB connection check failed after password prompt: %s", strings.TrimSpace(string(out)))
	}
	return nil
}

func runMariaDBPing(ctx context.Context, client string, options []string) ([]byte, error) {
	args := append([]string{}, options...)
	args = append(args, "--batch", "--skip-column-names", "-e", "SELECT 1")
	cmd := exec.CommandContext(ctx, client, args...)
	return cmd.CombinedOutput()
}

func looksLikeMissingPassword(out []byte) bool {
	lower := bytes.ToLower(out)
	return bytes.Contains(lower, []byte("access denied")) &&
		(bytes.Contains(lower, []byte("using password: no")) || bytes.Contains(lower, []byte("password")))
}

func PromptPassword(stdin io.Reader, stderr io.Writer, prompt string) (string, error) {
	if prompt == "" {
		prompt = "MariaDB password: "
	}
	_, _ = fmt.Fprint(stderr, prompt)
	echoDisabled := disableEcho()
	var password string
	_, err := fmt.Fscanln(stdin, &password)
	if echoDisabled {
		enableEcho()
		_, _ = fmt.Fprintln(stderr)
	}
	return password, err
}

func disableEcho() bool {
	cmd := exec.Command("stty", "-echo")
	cmd.Stdin = os.Stdin
	return cmd.Run() == nil
}

func enableEcho() {
	cmd := exec.Command("stty", "echo")
	cmd.Stdin = os.Stdin
	_ = cmd.Run()
}
