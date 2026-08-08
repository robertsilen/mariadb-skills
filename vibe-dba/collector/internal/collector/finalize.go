package collector

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

func FinalizeCollection(outputDir string, packageOpt, cleanupOpt OptionalBool, stdin io.Reader, stderr io.Writer, stdout io.Writer) error {
	promptReader := bufio.NewReader(stdin)
	packageData, err := ResolveOptionalBool(packageOpt, promptReader, stderr, "Do you want to package all collected data?")
	if err != nil {
		return err
	}

	var packagePath string
	if packageData {
		packagePath = filepath.Clean(outputDir) + ".tgz"
		if err := TarGzDir(outputDir, packagePath); err != nil {
			return err
		}
	}

	cleanupData, err := ResolveOptionalBool(cleanupOpt, promptReader, stderr, "Do you want to remove collected data?")
	if err != nil {
		return err
	}
	if cleanupData {
		if err := os.RemoveAll(outputDir); err != nil {
			return err
		}
	}

	switch {
	case packageData && cleanupData:
		_, _ = fmt.Fprintf(stdout, "Wrote %s and removed %s\n", packagePath, outputDir)
	case packageData:
		_, _ = fmt.Fprintf(stdout, "Wrote %s and %s\n", outputDir, packagePath)
	case cleanupData:
		_, _ = fmt.Fprintf(stdout, "Removed %s\n", outputDir)
	default:
		_, _ = fmt.Fprintf(stdout, "Wrote %s\n", outputDir)
	}
	return nil
}
