package collector

import (
	"bufio"
	"fmt"
	"io"
	"strconv"
	"strings"
)

type OptionalBool struct {
	Value bool
	IsSet bool
}

func (b *OptionalBool) Set(value string) error {
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		return err
	}
	b.Value = parsed
	b.IsSet = true
	return nil
}

func (b *OptionalBool) String() string {
	if b == nil || !b.IsSet {
		return ""
	}
	return strconv.FormatBool(b.Value)
}

func (b *OptionalBool) IsBoolFlag() bool {
	return true
}

func ResolveOptionalBool(opt OptionalBool, stdin io.Reader, stderr io.Writer, question string) (bool, error) {
	if opt.IsSet {
		return opt.Value, nil
	}
	return PromptYesNo(stdin, stderr, question)
}

func PromptYesNo(stdin io.Reader, stderr io.Writer, question string) (bool, error) {
	reader, ok := stdin.(interface {
		ReadString(byte) (string, error)
	})
	if !ok {
		reader = bufio.NewReader(stdin)
	}
	for {
		_, _ = fmt.Fprintf(stderr, "%s [y/N]: ", question)
		answer, err := reader.ReadString('\n')
		if err != nil {
			if strings.TrimSpace(answer) == "" {
				return false, nil
			}
			return false, err
		}
		switch strings.ToLower(strings.TrimSpace(answer)) {
		case "", "n", "no":
			return false, nil
		case "y", "yes":
			return true, nil
		}
	}
}
